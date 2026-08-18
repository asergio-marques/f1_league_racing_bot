---
description: "Task list for feature 045 — image test commands drawn from the league's own configuration"
---

# Tasks: Image test commands drawn from the league's own configuration

**Input**: Design documents from `specs/045-image-test-subcommands/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/command-surface.md](contracts/command-surface.md)

**Tests**: MANDATORY. Every implementation task below names the test that covers it, and that test must pass before the next task begins. No coverage is parked in the polish phase.

**Live Discord is out of scope**: no task here requires a running bot, a gateway connection or a real server. Every test runs under `pytest` with Discord stubbed. Judging that a picture *looks* right is done by hand against the PNG, per [quickstart.md](quickstart.md), and is not a task.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: US1, US2, US3 — maps to the user stories in [spec.md](spec.md)

## Path Conventions

Single project: `src/` and `tests/` at repository root, matching the repository as it stands.

---

## Phase 1: Setup

**Purpose**: Establish the baseline and rescue what the retired module holds that is still wanted.

- [X] T001 Record the baseline by running `pytest tests/ -q` from the repo root and noting the pass/skip counts, so any later failure is known to be new rather than pre-existing
- [X] T002 [P] Create `src/services/image_preview_data.py` holding only the long-text constants rescued from `src/services/image_sample_data.py` — `LONG_DRIVER_NAME`, `_VERDICT_TEXT_SHORT`, `_VERDICT_TEXT_FULL`, `_VERDICT_TEXT_OVER`, `_VERDICT_TEXT_HUGE`, `VERDICT_TEXT_NOT_PROVIDED` — which FR-032 still needs; covered by `tests/unit/test_image_preview_data.py` asserting each constant is non-empty and that the huge text exceeds the full text by an order of magnitude

**Checkpoint**: Baseline recorded; the constants worth keeping are out of the module due for retirement.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The one resolution path all eleven commands share. Every user story depends on it.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Create `src/services/image_preview_service.py` with the `PreviewContext`, `PreviewDriver` and `DirectoryFault` shapes of [data-model.md](data-model.md); covered by `tests/unit/test_image_preview_service.py` asserting field defaults and that `PreviewDriver.fabricated` defaults false
- [X] T004 Implement active-season and division-by-name resolution in `src/services/image_preview_service.py`, reading `season_service.get_active_season` then `get_divisions`, matching on name; covered in `tests/unit/test_image_preview_service.py` with a seeded season of two divisions, asserting the right one resolves and an unknown name refuses
- [X] T005 Implement round-by-number resolution in `src/services/image_preview_service.py`, reading `season_service.get_division_rounds` and matching `Round.round_number`; covered in `tests/unit/test_image_preview_service.py`, pinning `now` alongside every seeded round date
- [X] T006 Implement team and driver resolution in `src/services/image_preview_service.py` from `team_service.get_division_teams`, applying the all-or-nothing fabrication rule of FR-018 and FR-020 — every seat fabricated where the division has seated nobody, seats drawn as they stand otherwise; covered in `tests/unit/test_image_preview_service.py` with three cases: fully seated, partly seated, wholly unseated
- [X] T007 Implement nationality handling in `src/services/image_preview_service.py`, reading `nationality_required` from the signup settings, giving a fabricated driver a fabricated nationality only where the league collects one (FR-019); covered in `tests/unit/test_image_preview_service.py` with the setting both on and off
- [X] T008 Implement the nine refusals of [contracts/command-surface.md](contracts/command-surface.md) in `src/services/image_preview_service.py`, in the contracted evaluation order; covered in `tests/unit/test_image_preview_service.py` by one test per refusal plus the two ordering cases — a wrong round on a teamless division reports the round, and a mistyped division with a wrong round reports the division
- [X] T009 Assert in `tests/unit/test_image_preview_service.py` that every refusal returns before any render is attempted (FR-015), by injecting a render service that fails the test if called
- [X] T010 Implement asset-directory resolution in `src/services/image_preview_service.py`, reading the league's eight directories from `image_config_service.get_config` as the posting path does, and capturing the rejection the posting path discards as a `DirectoryFault` (FR-038, research R3); covered in `tests/unit/test_image_preview_service.py` with a good directory, a directory that does not exist, and a path escaping the project root
- [X] T011 Add the nested `test` `app_commands.Group` to `src/cogs/image_cog.py`, replacing the withdrawn `test` command, and extend `_verify_discord_group_limits()` to check the new group; covered by `tests/unit/test_image_cog_test_commands.py` asserting the group registers and the limit check includes it
- [X] T012 Implement division-name autocomplete for the `division` parameter in `src/cogs/image_cog.py`, modelled on `season_cog.round_add_track_autocomplete`; covered in `tests/unit/test_image_cog_test_commands.py` asserting it offers the active season's divisions and nothing from an archived one
- [X] T013 Implement the shared reply builder in `src/cogs/image_cog.py` — pictures, a line per picture, the notice block, the three-form artwork report of [contracts/command-surface.md](contracts/command-surface.md), and the fabrication notice; covered in `tests/unit/test_image_cog_test_commands.py` asserting a fallback, a rejected directory and a clean render each produce their distinct wording

**Checkpoint**: Resolution, refusals, asset directories and the command scaffold are in place. User stories can now proceed.

---

## Phase 3: User Story 1 — Preview a template against a real division (Priority: P1) 🎯 MVP

**Goal**: A manager previews the calendar and the lineup for one of their own divisions and sees their own rounds, tracks, dates, teams and drivers.

**Independent Test**: Seed a season with one division holding rounds and teams; invoke the calendar and lineup previews; confirm the drawings carry that division's own data and not invented data.

**Why these two**: they fabricate no outcome whatsoever, so they prove the whole live-data path without depending on any fabrication.

- [X] T014 [P] [US1] Implement the calendar preview in `src/services/image_preview_service.py`, passing the division's configured rounds in order to `image_calendar_service.resolve_drawing` and the league's directories to `build_fill_spec`; covered by `tests/unit/test_image_preview_calendar.py` asserting the drawing holds exactly the seeded rounds, in order, with their tracks and dates
- [X] T015 [P] [US1] Implement the lineup preview in `src/services/image_preview_service.py`, passing the division's teams and seated drivers to `image_lineup_service.resolve_drawing`; covered by `tests/unit/test_image_preview_lineup.py` asserting the drawing carries the seeded team and driver names, and that a wholly unseated division draws fabricated drivers instead
- [X] T016 [US1] Register `/images test calendar` and `/images test lineup` in `src/cogs/image_cog.py` against the resolution path and the shared reply builder; covered in `tests/unit/test_image_cog_test_commands.py` asserting each takes `division` alone, defers, and refuses on its own conditions (calendar on no configured round, lineup on no team beyond Reserve)
- [X] T017 [US1] Assert in `tests/unit/test_image_preview_calendar.py` that the calendar preview resolves track and flag assets through the league's configured directories and never the packaged ones (FR-035), by pointing a directory at a temporary folder and asserting the file drawn came from it

**Checkpoint**: US1 is shippable on its own. A manager can pretest a calendar and a lineup against their real configuration, with real artwork.

---

## Phase 4: User Story 2 — Preview the kinds whose data a league cannot configure (Priority: P2)

**Goal**: A manager previews the results, standings, attendance sheet and verdicts for a round that has not been run, drawn over their own drivers and teams.

**Independent Test**: Seed a division with teams, seated drivers and a round; preview each of the four kinds; confirm every picture carries the real drivers and teams with fabricated performance data.

**Note on `rsvp`**: it fabricates nothing, so it belongs with US1 by shape, but it is round-scoped like US2. It is placed first here as the simplest round-scoped preview, proving round resolution end to end before any fabrication is involved.

- [X] T018 [US2] Implement the check-in preview in `src/services/image_preview_service.py`, passing the round's own format, track, schedule and deadline to `image_rsvp_service.resolve_drawing` and fabricating nothing; covered by `tests/unit/test_image_preview_rsvp.py` asserting the drawing carries the seeded round's values and that its session list follows the round's format
- [X] T019 [P] [US2] Implement fabricated classifications in `src/services/image_preview_data.py`, honouring FR-024 — every drawn driver placed once, positions `1..n` with no gap, intervals increasing with position, non-finishers renumbered to the bottom; covered by `tests/unit/test_image_preview_data.py` asserting each invariant over several driver counts
- [X] T020 [P] [US2] Implement fabricated attendance records in `src/services/image_preview_data.py` for every driver over the rounds up to and including the named one, covering the range of states a sheet carries; covered in `tests/unit/test_image_preview_data.py` asserting no record falls after the named round
- [X] T021 [P] [US2] Implement fabricated verdicts in `src/services/image_preview_data.py`, drawing the sanction only from `+5s`, `+10s`, `-3s` and `DSQ` (FR-034) and the free text at the five lengths of FR-032; covered in `tests/unit/test_image_preview_data.py` asserting no sanction outside that vocabulary is ever produced across many draws
- [X] T022 [US2] Implement the results preview in `src/services/image_preview_service.py`, generating one drawing per session of the round's format via `image_results_service.resolve_drawing`, qualifying sessions from the qualifying template and races from the race template; covered by `tests/unit/test_image_preview_results.py` asserting the session count and template choice for the normal, sprint and endurance formats
- [X] T023 [US2] Implement the standings preview in `src/services/image_preview_service.py`, fabricating results for every round up to and including the named one and generating both the drivers and the constructors drawing via `image_standings_service.resolve_drawing`; covered by `tests/unit/test_image_preview_standings.py` asserting both drawings are produced and the grid holds the division's own calendar length (FR-026, SC-005)
- [X] T024 [US2] Implement the attendance preview in `src/services/image_preview_service.py` via `image_attendance_service.resolve_drawing`, drawing a driver's flag only where the league collects nationality (FR-028); covered by `tests/unit/test_image_preview_attendance.py` with the setting both on and off
- [X] T025 [US2] Implement the verdict preview in `src/services/image_preview_service.py` via `image_verdict_service.resolve_drawing`, one drawing per case, the driver drawn from the division's own and the session from those the round is run over (FR-033, research R7); covered by `tests/unit/test_image_preview_verdict.py`
- [X] T026 [US2] Register `/images test rsvp`, `results`, `standings`, `attendance` and `verdict` in `src/cogs/image_cog.py`, each taking `division` and `round`; covered in `tests/unit/test_image_cog_test_commands.py` asserting parameters, deferral and the team-list refusal on the four that need teams

**Checkpoint**: US1 and US2 both work independently. Nine of the eleven commands are live.

---

## Phase 5: User Story 3 — Preview the weather graphics against a real round (Priority: P3)

**Goal**: A manager previews each forecast phase, and the mystery notice, for one of their rounds, with the fabricated forecast covering everything that round's format admits.

**Independent Test**: Seed a division holding one round of each format; preview each phase for each; confirm the forecast covers the session weather types and slot types the format admits, and that the mystery refusals fire both ways.

- [X] T027 [P] [US3] Implement fabricated phase 1 likelihood in `src/services/image_preview_data.py`, a value in `[0, 100]` and deliberately not a whole percentage (FR-029); covered in `tests/unit/test_image_preview_data.py` asserting the range and the fractional part over many draws
- [X] T028 [P] [US3] Implement fabricated phase 2 session weather in `src/services/image_preview_data.py`, one type per session, all three types across a sprint round's four sessions and two across a two-session round (FR-030); covered in `tests/unit/test_image_preview_data.py` for each non-mystery format
- [X] T029 [P] [US3] Implement fabricated phase 3 slots in `src/services/image_preview_data.py`, within each session type's `MAX_SLOTS` and with all five slot types appearing across the round (FR-031, research R6); covered in `tests/unit/test_image_preview_data.py` asserting all five appear for the normal, sprint and endurance formats and that no session exceeds its ceiling
- [X] T030 [US3] Implement the four weather previews in `src/services/image_preview_service.py` via `image_weather_service.resolve_drawing`, choosing the sprint template of the phase for a sprint round and the plain template otherwise; covered by `tests/unit/test_image_preview_weather.py` asserting the template choice per format and that the mystery notice carries no session
- [X] T031 [US3] Register `/images test weather-p1`, `weather-p2`, `weather-p3` and `weather-mystery` in `src/cogs/image_cog.py`; covered in `tests/unit/test_image_cog_test_commands.py` asserting the three phases refuse a mystery round and the mystery notice refuses a non-mystery round (FR-012, FR-013)

**Checkpoint**: All three stories work independently. All eleven commands are live.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Retire what this feature replaces, and bring the documents back into step.

- [X] T032 Retire `src/services/image_sample_data.py`. **Larger than first estimated**: four other suites still import it — `tests/integration/test_image_module_flow.py` (four call sites), `tests/unit/test_image_calendar_fill.py` and `tests/unit/test_image_results_fill.py` use `build_spec` and `build_results_drawing` as convenient fixtures, independently of the withdrawn command. Each needs its own drawing built before the module can go. The tests of the withdrawn command itself were already removed at US1, because leaving them red would have blocked every task after it
- [X] T033 Reshape `TEST_KIND_TEMPLATES` in `src/models/image_constants.py` to the eleven commands, or remove it if the preview service no longer reads it; covered by whichever existing test asserts template coverage, updated in the same change
- [X] T034 Run `pytest tests/ -q` and compare against the T001 baseline; every failure is real until confirmed on a clean tree
- [X] T035 [P] Update `README.md` — the `/images test` command reference, the Inkscape prerequisite note, the lineup authoring note and the "checking your work" note, per the spec's Documentation impact section
- [X] T036 [P] Update `docs/how-to/configuring-the-image-module.md` — the walkthrough steps, the checklist, the troubleshooting table, and above all the standing warning that `/images test` never shows a league's own artwork, which FR-035 withdraws
- [X] T037 [P] Update `docs/how-to/configuring-the-results-module.md` — the note pointing at `/images test standings`
- [X] T038 [P] Add an entry to `docs/wip-specs/known_issues.md` recording the three defects found while building the previews (research R3 and two found in build). All three were subsequently fixed at the author's direction — see the out-of-band phase below
- [X] T039 Add a rasterisation test in `tests/unit/test_image_preview_render.py` asserting each of the eleven kinds produces a PNG file with non-zero dimensions from a seeded division, skipping cleanly where the rasteriser is absent (Rule XIV.14 — the check is against the raster, never the SVG). Judging whether a picture *looks* right stays out of the task list and is done by hand against the PNG per [quickstart.md](quickstart.md)
- [X] T040 Assess whether the constitution needs amending, through `/speckit-constitution`. **Outcome: it does not.** Audited on 2026-08-18: within the ratified body, the only mentions of the withdrawn command are two entries in the versioned entity inventory, which are historical records of what increments 043 and 042 delivered and were accurate when written. No principle, no rule and no MUST is touched. A PATCH to v5.0.1 was raised and then withdrawn on that finding, the branch being unpushed and unmerged — the same ground the constitution's own v5.0.0 entry gives for absorbing a version nobody outside the branch has read. The constitution stands at v5.0.0

---

---

## Out-of-band: three shipped defects fixed at the author's direction (2026-08-18)

Not planned by this feature. Found while building the previews, evaluated, and fixed on the author's explicit choice of direction for each. Recorded here so the work is not invisible in the task record.

- [X] T041 Correct the `signup_records` join at its three sites — `src/services/image_lineup_post.py` and two in `src/services/image_results_post.py` — to join on `(server_id, discord_user_id)`, the key the table actually has; covered by `tests/unit/test_image_post_signup_join.py`, which runs the queries against a migrated database rather than stubbing their rows
- [X] T042 Point `_nationality_collected` in `src/services/image_results_post.py`, and the inline copy in `src/services/image_lineup_post.py`, at `signup_module_settings`; covered in `tests/unit/test_image_post_signup_join.py` with the switch on, off, and absent
- [X] T043 Add `resolve_configured_directories` and `spec_builder_with_faults` to `src/services/image_render_service.py`, log the reason a configured directory was rejected, carry it onto `FillSpec.asset_directory_faults`, and have `src/utils/svg_fill.py` name it instead of calling the class unconfigured; applied at all six posting modules; covered by `tests/unit/test_image_directory_faults.py`
- [X] T044 Record all three in `docs/wip-specs/known_issues.md` as found-and-fixed, with the coverage gap that let two of them ship, and add the rejected-directory reporting rule to `docs/wip-specs/image_module_specification.md`

## Dependencies

**Story completion order**: Foundational → US1 → US2 → US3 → Polish.

- **Phase 2 blocks everything.** All eleven commands share the resolution path, the refusals and the asset directories.
- **US1 depends on Phase 2 alone** and is shippable by itself.
- **US2 depends on Phase 2**, not on US1 — the two touch different preview functions and different test modules. It is sequenced second by priority, not by dependency.
- **US3 depends on Phase 2**, not on US1 or US2, for the same reason.
- **Polish depends on all three**, because T032 cannot delete the retired module until nothing imports it and T034 is the whole-suite check.
- **T040 is last of all**, per the user's direction that the amendment is needed only at the end, if anything.

## Parallel opportunities

- **Phase 2**: T003 must land first; T004–T010 touch one service and one test module, so they are sequential. T011–T013 touch the cog and may proceed alongside T004–T010.
- **US1**: T014 and T015 are `[P]` — different preview functions, different test modules.
- **US2**: T019, T020 and T021 are `[P]` — three independent fabricators in one module, each with its own tests. T022–T025 follow them and are sequential within the service.
- **US3**: T027, T028 and T029 are `[P]` for the same reason.
- **Polish**: T035, T036, T037 and T038 are `[P]` — four separate documents.
- **Across stories**: once Phase 2 is done, US1, US2 and US3 could be built in parallel by separate workers. The fabricators (T019–T021, T027–T029) all live in `image_preview_data.py`, so parallel work there needs coordination on one file.

## Implementation strategy

**MVP is US1** — Phases 1, 2 and 3, tasks T001 to T017. That delivers the calendar and lineup previews drawn against a real division with the league's real artwork, which is the whole claim of the feature. Everything after adds kinds, not capability.

**Incremental delivery**: each story's checkpoint is a genuine stopping point. Stopping after US1 leaves nine commands withdrawn and two working, so the withdrawal of `/images test <kind>` must land in the same increment as the last story it replaces — do not ship the removal ahead of the replacements.

**Documentation lands with the code, not after it.** T035–T037 are in the polish phase because they depend on all eleven commands existing, not because they are optional. The README and the how-to guides describe the bot as it is, and become wrong the moment this ships.
