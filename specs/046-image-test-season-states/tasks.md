---

description: "Task list for feature 046 — image previews across every season state"
---

# Tasks: Image previews across every season state

**Input**: Design documents from `/specs/046-image-test-season-states/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/commands.md](contracts/commands.md), [quickstart.md](quickstart.md)

**Tests**: MANDATORY. Every implementation task below is covered by a unit test named in the task itself or by its own test task immediately preceding it. No coverage is parked in the polish phase, and each test passes before the next task begins.

**Live Discord is out of scope**: no task here runs the bot, connects a gateway, or posts to a real server. Discord is stubbed — `tests/unit/test_image_cog_test_commands.py` already carries `_Interaction`, `_Response` and `_Followup` stubs to build on. Full system checking happens by hand, afterwards, outside this list.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel — different file, no dependency on an incomplete task
- **[Story]**: US1 (pending approval), US2 (no season), US3 (test mode)

## Path Conventions

Single project: `src/` and `tests/` at the repository root, per [plan.md](plan.md).

---

## Phase 1: Setup

**Purpose**: Establish the baseline the change is measured against. No scaffolding — this is an existing codebase with the harness already in place.

- [X] T001 Run `pytest tests/ -q` from the repo root and record the pass/skip counts in the working notes; the suite stood at 2209 passed, 1 skipped on 2026-08-19, and any deviation is investigated on a clean tree before work begins

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared seams all three stories rest on. **No user story may begin until this phase is complete.**

- [X] T002 [P] Extend `PreviewContext` with `rounds: list`, `fabricated_league: bool`, `season_pending_approval: bool` and `drivers_without_nationality: int`, all defaulted so existing construction sites keep working, in `src/services/image_preview_service.py`
- [X] T003 [P] Add a `PREVIEW_KINDS` classification mapping each of the eleven kinds to `needs_round`, `draws_roster` and `format_demanded`, per the table in `specs/046-image-test-season-states/data-model.md`, in `src/models/image_constants.py`
- [X] T004 [P] Test `PREVIEW_KINDS` totality — all eleven kinds present, no extras, and `draws_roster` true for exactly `lineup`, `results`, `standings`, `attendance`, `verdict` — in `tests/unit/test_image_preview_kinds.py`
- [X] T005 Populate `context.rounds` once in `resolve_context`, and change `build_calendar_preview` and `build_attendance_preview` to read it instead of re-calling `get_division_rounds(context.division_id)`, in `src/services/image_preview_service.py`
- [X] T006 Test that the calendar and attendance previews draw from `context.rounds` and issue no second round query, by handing a context whose `rounds` differ from what the division holds, in `tests/unit/test_image_preview_service.py`
- [X] T007 [P] Add `SeasonService.get_previewable_season(server_id)` selecting `status IN ('ACTIVE','SETUP')` with `ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1`, leaving `get_setup_or_active_season` untouched, in `src/services/season_service.py`
- [X] T008 [P] Test `get_previewable_season` returns the ACTIVE season when both exist, the SETUP season when only it exists, and `None` when neither does — including that COMPLETED and CANCELLED seasons are never returned — in `tests/unit/test_season_previewable_lookup.py`

**Checkpoint**: `pytest tests/ -q` green, and the preview behaves exactly as it did before — this phase changes no behaviour.

---

## Phase 3: User Story 1 - Preview a season that is still pending approval (Priority: P1) 🎯 MVP

**Goal**: A season built but not yet approved is previewable in all eleven kinds, drawn exactly as an approved one.

**Independent test**: Build a SETUP season with one division holding rounds and seated teams, invoke every preview for that division, and confirm each returns what an equivalent ACTIVE season returns.

### Tests for User Story 1 (MANDATORY) ⚠️

- [X] T009 [P] [US1] Test that `resolve_context` resolves a SETUP season, sets `season_pending_approval` true, and returns the seeded season number, using the existing `_seed_season(status="SETUP")` helper, in `tests/unit/test_image_preview_service.py`
- [X] T010 [P] [US1] Test that an ACTIVE season outranks a later SETUP one — the context carries the ACTIVE season's number and divisions, and `season_pending_approval` is false — in `tests/unit/test_image_preview_service.py`
- [X] T011 [P] [US1] Test that all six of feature 045's refusals still raise on a SETUP season, each with its own reason and before any render, in `tests/unit/test_image_preview_service.py`
- [X] T012 [P] [US1] Test that a COMPLETED-only and a CANCELLED-only server both resolve no season, so neither is drawn, in `tests/unit/test_image_preview_service.py`

### Implementation for User Story 1

- [X] T013 [US1] Replace `get_active_season` with `get_previewable_season` in `resolve_context` and set `context.season_pending_approval` from the resolved status, keeping the existing `REASON_NO_SEASON` refusal in place for now, in `src/services/image_preview_service.py`
- [X] T014 [US1] Point `_division_autocomplete` at `get_previewable_season` so completion offers the divisions of whichever season is drawn, in `src/cogs/image_cog.py`
- [X] T015 [US1] Add the season number to the preview header and a pending-approval note to the reply when `season_pending_approval` is set, in `_send_preview` in `src/cogs/image_cog.py`
- [X] T016 [P] [US1] Test the autocomplete offers a SETUP season's divisions, and the reply carries the season number and the pending-approval note, against the stubbed interaction, in `tests/unit/test_image_cog_test_commands.py`

**Checkpoint**: US1 is independently shippable. A league can preview before approving; a season-less server still refuses as it does today.

---

## Phase 4: User Story 2 - Preview on a server with no season at all (Priority: P2)

**Goal**: On a server with no season, ten of the eleven kinds draw a fabricated league over the server's own configured teams; the five that draw a roster refuse where the team list is bare.

**Independent test**: On a server holding configured teams and no season, invoke each of the eleven previews with no arguments and confirm each returns a picture whose team names are the server's own and whose division, calendar, formats, round number and drivers are invented.

### Tests for User Story 2 (MANDATORY) ⚠️

- [X] T017 [P] [US2] Test `get_previous_season_number` returns the highest committed number, ignores SETUP seasons, and yields 0 on a server that has never held one, in `tests/unit/test_season_previewable_lookup.py`
- [X] T018 [P] [US2] Test the fabricated league under a seeded `random.Random` and a pinned `now` — season number derived, team names taken from `default_teams` with Reserve excluded, every seat filled, and the calendar carrying more than one format — in `tests/unit/test_image_preview_league.py`
- [X] T019 [P] [US2] Test that the fabricated round suits the kind across several seeds: `weather-mystery` always fabricates a MYSTERY round and `weather-p1`/`-p2`/`-p3` never do, so neither is ever refused for the round's format, in `tests/unit/test_image_preview_league.py`
- [X] T020 [P] [US2] Test that two unseeded invocations differ in division name, calendar, round number and driver names, and agree in team names, in `tests/unit/test_image_preview_league.py`
- [X] T021 [P] [US2] Test the fabricated track is one the `tracks` table carries, so its country resolves through `_country_of`, in `tests/unit/test_image_preview_league.py`
- [X] T022 [P] [US2] Test the team-list split on a bare server: `lineup`, `results`, `standings`, `attendance` and `verdict` raise `REASON_NO_SERVER_TEAMS`, while `calendar`, `rsvp` and the four weather kinds draw, in `tests/unit/test_image_preview_league.py`
- [X] T023 [P] [US2] Test parameter handling: on a season-less server a supplied division and round are disregarded and omitting both is accepted; where a season exists, omitting a required one raises `REASON_MISSING_INPUT`, in `tests/unit/test_image_preview_service.py`
- [X] T024 [P] [US2] Test that every table is byte-identical before and after running all eleven previews on a season-less server, in `tests/unit/test_image_preview_league.py`

### Implementation for User Story 2

- [X] T025 [US2] Add `SeasonService.get_previous_season_number(server_id)` returning `MAX(season_number)` over rows whose status is not SETUP, defaulting to 0, in `src/services/season_service.py`
- [X] T026 [US2] Create the fabricated league factory `build_fabricated_context(bot, server_id, *, kind, rng=None, now=None)` returning a complete `PreviewContext` with `fabricated_league` set — reading `default_teams` and `tracks`, deriving the season number from T025, and randomising division, tier, calendar, formats, round, schedule and driver names — in `src/services/image_preview_league.py`
- [X] T027 [US2] Add `REASON_MISSING_INPUT` and `REASON_NO_SERVER_TEAMS`, withdraw `REASON_NO_SEASON`, and restructure `resolve_context` to the resolution order in `specs/046-image-test-season-states/contracts/commands.md` — dispatching to the factory where no season exists — in `src/services/image_preview_service.py`
- [X] T028 [US2] Make `division` and `round` optional on all eleven commands and pass each command's kind into `_run_preview`, replacing the ad-hoc `require_rounds` / `require_teams` / `require_mystery` flags with the `PREVIEW_KINDS` lookup, in `src/cogs/image_cog.py`
- [X] T029 [US2] Add the fabricated-league banner to the reply, stating that no season exists, that the league is invented, and that the team names are the server's own, in `_send_preview` in `src/cogs/image_cog.py`
- [X] T030 [US2] Test that each of the eleven builders returns **no `problem` outcome** over a fabricated context — one assertion per kind, since this is Rule XIV.3 applied to data the feature itself invented — in `tests/unit/test_image_preview_league.py`
- [X] T031 [P] [US2] Test the cog surface on a season-less server: each command invoked with no arguments returns a picture, and the reply carries the fabricated-league banner, in `tests/unit/test_image_cog_test_commands.py`

**Checkpoint**: US1 and US2 both work. The previews are usable at every point in a league's life.

---

## Phase 5: User Story 3 - Preview under test mode (Priority: P3)

**Goal**: Mock drivers are drawn by their mock names in all three season states, and a driver drawn without a flag is accounted for in the reply.

**Independent test**: Seat a division with mock drivers under test mode in a season pending approval, preview every kind that draws drivers, and confirm each picture carries the mock names.

### Tests for User Story 3 (MANDATORY) ⚠️

- [X] T032 [P] [US3] Test that a division seated via `test_roster_service.add_test_driver` in a SETUP season draws every mock driver's `test_display_name` in the lineup, results, standings, attendance and verdict previews, in `tests/unit/test_image_preview_testmode.py`
- [X] T033 [P] [US3] Test that a division seated wholly with mock drivers leaves `fabricated_drivers` false — a mock driver is a seated driver, never an empty seat — in `tests/unit/test_image_preview_testmode.py`
- [X] T034 [P] [US3] Test that `drivers_without_nationality` counts seated drivers with no nationality where the league collects it, and is zero where it does not, in `tests/unit/test_image_preview_testmode.py`
- [X] T035 [P] [US3] Test that a season-less server draws the fabricated league identically whether the test-mode flag is set or clear, in `tests/unit/test_image_preview_testmode.py`

### Implementation for User Story 3

- [X] T036 [US3] Populate `context.drivers_without_nationality` in `_drivers_from_teams`, counting seated drivers drawn with no flag where `nationality_collected` holds, in `src/services/image_preview_service.py`
- [X] T037 [US3] Add the no-nationality tally line to the reply when the count is non-zero, naming that a test-mode driver records none, in `_send_preview` in `src/cogs/image_cog.py`
- [X] T038 [P] [US3] Test the reply carries the tally line against the stubbed interaction, in `tests/unit/test_image_cog_test_commands.py`

**Checkpoint**: all three stories complete.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Bring the documents into step and confirm the suite. The wip-spec is **already updated** — the rules landed with the specification on 2026-08-19 — so only the league-facing documents remain.

- [X] T039 [P] Correct the `/images test` command reference in `README.md`: both parameters optional, the active-season requirement withdrawn, and the three season states described
- [X] T040 [P] Correct `docs/how-to/configuring-the-image-module.md`: the walkthrough and checklist currently place the previews after a season exists, and the previews now come before one — this changes the order of the job the guide owns
- [X] T041 [P] Correct the "Previewing images" section of `docs/how-to/test-mode.md`, withdrawing "a server with no active season is refused" and the instruction to build and approve a season before previewing
- [X] T042 [P] Check `docs/how-to/configuring-the-results-module.md`, which points at `/images test standings`, and correct it if the changed parameters make its wording wrong
- [X] T043 Raise the PATCH constitution amendment owed from feature 045 via `/speckit-constitution` — the versioned entity inventory still calls the test subcommands parameter *values* under New Entities (v4.8.0) and (v4.7.0); never edit `.specify/memory/constitution.md` by hand
- [X] T044 Run `pytest tests/ -q` from the repo root and confirm the whole suite is green against the T001 baseline
- [X] T045 Drive one fabricated-league preview through `src/services/image_render_service.py` from a scratch script and inspect the exported **PNG** by eye — never the SVG in a browser, which hides the flowed text, substituted fonts and unresolvable hrefs the rasteriser exposes. This is an eye check rather than an assertion, so it belongs in a scratch script and not under `tests/`

### Defect found while eye-checking, and fixed on the user's instruction

T045 turned up a defect that predates this feature and reaches the posting path, not only the preview. It was first logged to `known_issues.md` under the standing "record, do not fix" rule; the user directed that it be fixed.

- [X] T046 Gather the field names a removed node takes off the canvas — every `@id` in its subtree, and an `inkscape:label` only where the node is a layer — in `_removed_field_ids`, and subtract those from the mandatory sweep instead of subtracting `spec.remove` alone, in `src/services/image_render_service.py`
- [X] T047 Test that a division fielding no reserve driver renders against the **shipped** `resources/templates/lineup_template.svg`, that a fielded reserve driver is still demanded, and that the sweep reaches no field outside what was removed, in `tests/unit/test_image_render_removed_groups.py`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)** → no dependencies
- **Phase 2 (Foundational)** → depends on Phase 1. **Blocks every user story.**
- **Phase 3 (US1)** → depends on Phase 2
- **Phase 4 (US2)** → depends on Phase 2. Builds on US1's season lookup but does not require US1's reply work
- **Phase 5 (US3)** → depends on Phase 2; its mock-driver tests are most meaningful once US1 lands
- **Phase 6 (Polish)** → depends on all stories being complete

### User Story Dependencies

- **US1 (P1)**: independent once Phase 2 is done — the MVP
- **US2 (P2)**: needs `get_previewable_season` from Phase 2 to detect *no* season, and `context.rounds` from T005 for the fabricated calendar to reach the builders. Otherwise independent of US1
- **US3 (P3)**: strictly speaking independent, but T032's fixture seats mock drivers in a SETUP season, so it reads best after US1

### Within Each User Story

Tests come before the implementation they cover, and each test passes before the next task starts. Within US2 the order is forced: T025 → T026 → T027 → T028 → T029, because the factory needs the season number, the resolution needs the factory, the cog needs the resolution, and the reply needs the cog.

### Parallel Opportunities

- **Phase 2**: T002, T003, T004, T007 and T008 are four different files — all parallel. T005 and T006 are sequential against T002
- **Phase 3**: T009–T012 are independent tests; T016 is a different file from T013–T015
- **Phase 4**: T017–T024 are all tests and all parallel. The implementation chain T025–T029 is strictly sequential; T030 and T031 parallelise after it
- **Phase 5**: T032–T035 parallel; T038 parallel with T036–T037
- **Phase 6**: T039–T042 are four different documents — all parallel

---

## Parallel Example: User Story 2

```
# The eight tests of US2, all independent files or independent cases:
T017  get_previous_season_number            tests/unit/test_season_previewable_lookup.py
T018  fabricated league under a seed        tests/unit/test_image_preview_league.py
T019  round format suits the kind           tests/unit/test_image_preview_league.py
T020  freshness across invocations          tests/unit/test_image_preview_league.py
T021  track resolves a country              tests/unit/test_image_preview_league.py
T022  the six-versus-five team-list split   tests/unit/test_image_preview_league.py
T023  parameter handling                    tests/unit/test_image_preview_service.py
T024  nothing is written                    tests/unit/test_image_preview_league.py

# Then, strictly in order:
T025 → T026 → T027 → T028 → T029

# Then in parallel again:
T030  no problem outcome, per kind          tests/unit/test_image_preview_league.py
T031  cog surface on a bare server          tests/unit/test_image_cog_test_commands.py
```

---

## Implementation Strategy

### MVP first (User Story 1 only)

Phases 1–3 alone are a shippable increment: a league can preview every kind before approving a season, which is the single most-wanted case and the one that costs least to build. A season-less server keeps refusing, exactly as today, so nothing regresses.

### Incremental delivery

1. Phases 1–2 → no behaviour change, suite green
2. Phase 3 → **release candidate**: pending-approval previews work
3. Phase 4 → season-less previews work; the largest phase by some distance
4. Phase 5 → test-mode reporting sharpened
5. Phase 6 → documents in step, suite green, one PNG verified by eye

### Where the risk is

**T030 is the task most likely to fail, and it should be attempted early rather than last.** Rule XIV.3 obliges every mandatory field of all eleven catalogues to be resolvable, and a fabricated context must satisfy them from data the feature itself invented. Consider writing T030 against a stub context before T026 is finished, so the factory is built against the assertion rather than validated after it.

**T022 encodes a decision the user corrected on 2026-08-19.** Six kinds draw on a bare server and five refuse, and the two easily-mistaken entries are `rsvp` (draws no roster, so it draws) and `verdict` (draws one, so it refuses). Verify against the builders, not against feature 045's prose, which names a different set.

---

## Notes

- `src/services/image_preview_data.py` is **not** touched. Feature 045 owns the outcome fabrication; this feature invents a league around it.
- No migration, no schema change, no new database entity.
- Two defects found while planning are logged in `docs/wip-specs/known_issues.md` and are **not** fixed here: `get_setup_or_active_season` ordering, and `get_divisions` returning cancelled divisions.
- Invoke the `close-out` skill before reporting the feature complete.
