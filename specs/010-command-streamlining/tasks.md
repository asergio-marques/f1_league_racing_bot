# Tasks: Command Streamlining & QoL Improvements

**Input**: Design documents from `/specs/010-command-streamlining/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no incomplete dependencies)
- **[Story]**: User story this task belongs to (US1–US9)
- Exact file paths included in every description

---

## Phase 1: Setup

**Purpose**: Database schema foundation required before any code changes.

- [X] T001 Create migration `src/db/migrations/007_cancellation_status.sql` — two `ALTER TABLE` statements adding `status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','CANCELLED'))` to `divisions` and `rounds`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Model fields, service methods, and message-builder helpers shared across all user stories. Must be complete before any user-story phase begins.

- [X] T002 [P] Add `status: str` field (default `"ACTIVE"`) to `Division` dataclass in `src/models/division.py`
- [X] T003 [P] Add `status: str` field (default `"ACTIVE"`) to `Round` dataclass in `src/models/round.py`
- [X] T004 Update `SeasonService.create_season` in `src/services/season_service.py` — remove `start_date` parameter; default to `date.today()` internally; update all call sites
- [X] T005 Add `SeasonService.renumber_rounds(division_id: int)` in `src/services/season_service.py` — fetches all rounds for the division, sorts by `scheduled_at` ascending, atomically rewrites `round_number` 1…N
- [X] T006 [P] Add `format_division_list(divisions: list[Division]) -> str` helper in `src/utils/message_builder.py` — produces a formatted string: one line per division with name, role mention, forecast channel mention
- [X] T007 [P] Add `format_round_list(rounds: list[Round]) -> str` helper in `src/utils/message_builder.py` — produces a formatted string: one line per round with round number, track (or "TBD"), format, and datetime (UTC)
- [X] T008 Create `division = app_commands.Group(name="division", ...)` in `src/cogs/season_cog.py` and move the existing `division_add` command into it as a `@division.command(name="add")` subcommand — no behaviour changes yet, only structural migration

---

## Phase 3: User Story 1 — Simplified Season Setup

**Story goal**: `/season setup` accepts no parameters and rejects if any season already exists.

**Independent test**: Run `/season setup` with no parameters on a clean server — season enters SETUP. Run again — rejected with conflict message.

- [X] T009 [US1] Wrap existing season commands (`season_setup`, `season_review`, `season_approve`, `season_status`) into an `app_commands.Group(name="season", ...)` in `src/cogs/season_cog.py`; rename each to `setup`, `review`, `approve`, `status` respectively
- [X] T010 [US1] Remove `start_date` and `num_divisions` parameters from `/season setup` in `src/cogs/season_cog.py`; remove `PendingConfig.start_date` field; remove division-slot pre-allocation loop; update the call to `season_service.create_season` to pass no `start_date`

---

## Phase 4: User Story 2 — Auto-Derived Round Numbers

**Story goal**: `/round add` has no `round_number` parameter; round position is derived from `scheduled_at`. Amending `scheduled_at` triggers renumbering.

**Independent test**: Add three rounds out of date order in one division; confirm numbers 1, 2, 3 are assigned in ascending date order on each confirmation.

- [X] T011 [US2] Delete `DuplicateRoundView` class and `_rounds_insert_before`, `_rounds_insert_after`, `_rounds_replace` helper functions from `src/cogs/season_cog.py`
- [X] T012 [US2] Create `round = app_commands.Group(name="round", ...)` in `src/cogs/season_cog.py`; move `round_add` into it as `@round.command(name="add")`; remove `round_number` parameter; compute insertion position by appending the new round then calling `renumber_rounds`; include the assigned round number in the confirmation message
- [X] T013 [P] [US2] In `/round amend` in `src/cogs/amendment_cog.py`, call `season_service.renumber_rounds(division_id)` after any change to `scheduled_at`, before sending the confirmation response

---

## Phase 5: User Story 3 — Post-Modification Feedback

**Story goal**: Every division mutation shows the full division list; every round mutation shows the full round list for the affected division.

**Independent test**: After `/division add`, confirm the response contains a list of all divisions. After `/round add`, confirm the response contains all rounds for that division.

- [X] T014 [US3] Append `format_division_list(...)` output to the confirmation response of `/division add` in `src/cogs/season_cog.py`
- [X] T015 [P] [US3] Append `format_round_list(...)` output to the confirmation response of `/round add` in `src/cogs/season_cog.py`
- [X] T016 [P] [US3] Append `format_round_list(...)` output to the confirmation response of `/round amend` in `src/cogs/amendment_cog.py`

---

## Phase 6: User Story 4 — Division Duplication During Setup

**Story goal**: `/division duplicate` copies all rounds with a datetime offset into a new division; setup mode only.

**Independent test**: Duplicate a division with +2 day, −1.5 hour offset; verify all rounds in the new division have datetimes shifted by exactly that amount.

- [X] T017 [US4] Add `SeasonService.duplicate_division(division_id, name, role_id, forecast_channel_id, day_offset: int, hour_offset: float) -> Division` in `src/services/season_service.py` — copies all rounds from source division with shifted `scheduled_at`; calls `renumber_rounds` on new division; returns new `Division`
- [X] T018 [US4] Add `/division duplicate` as `@division.command(name="duplicate")` in `src/cogs/season_cog.py` — parameters: `source_name`, `new_name`, `role`, `forecast_channel`, `day_offset: int`, `hour_offset: float`; reject if season not SETUP; reject if `new_name` already exists; call `duplicate_division`; warn if any shifted datetime is in the past or if two rounds share a `scheduled_at`; respond with `format_division_list` + `format_round_list` for the new division

---

## Phase 7: User Story 5 — Division and Round Deletion During Setup

**Story goal**: `/division delete` removes a division and its rounds; `/round delete` removes a single round and renumbers. Both are setup-only.

**Independent test**: Add a division with two rounds. Delete round 1; verify round 2 becomes round 1 and the round list is shown. Delete the division; verify division list is empty.

- [X] T019 [US5] Add `SeasonService.delete_division(division_id: int)` in `src/services/season_service.py` — cascade-deletes all rounds, sessions, phase results, forecast messages, and then the division row for the given `division_id`
- [X] T020 [P] [US5] Add `SeasonService.delete_round(round_id: int)` in `src/services/season_service.py` — deletes the round row (and any child sessions/phase_results), then calls `renumber_rounds` for the round's division
- [X] T021 [US5] Add `/division delete` as `@division.command(name="delete")` in `src/cogs/season_cog.py` — parameter: `name`; reject if season not SETUP; call `delete_division`; respond with `format_division_list`
- [X] T022 [P] [US5] Add `/round delete` as `@round.command(name="delete")` in `src/cogs/season_cog.py` — parameters: `division_name`, `round_number`; reject if season not SETUP; call `delete_round`; respond with `format_round_list` for the affected division

---

## Phase 8: User Story 6 — In-Season Division and Round Cancellation

**Story goal**: `/division cancel` and `/round cancel` require `CONFIRM`, mark the target `CANCELLED`, and post a notice to the forecast channel without pinging the division role. Active seasons only.

**Independent test**: Cancel a round by its number with `confirm:CONFIRM`; verify the forecast channel receives a notice with no role mention and the round is skipped by the scheduler.

- [X] T023 [US6] Update `src/services/scheduler_service.py` — add `status != 'CANCELLED'` guard to the phase-scheduling query for rounds; ensure divisions with `status = 'CANCELLED'` are entirely excluded from the scheduling loop
- [X] T024 [US6] Add `SeasonService.cancel_division(division_id: int)` in `src/services/season_service.py` — sets `divisions.status = 'CANCELLED'`; writes an `AUDIT` entry recording actor, division, change type `DIVISION_CANCELLED`
- [X] T025 [P] [US6] Add `SeasonService.cancel_round(round_id: int)` in `src/services/season_service.py` — sets `rounds.status = 'CANCELLED'`; writes an `AUDIT` entry recording actor, division, round, change type `ROUND_CANCELLED`
- [X] T026 [US6] Add `/division cancel` as `@division.command(name="cancel")` in `src/cogs/season_cog.py` — parameters: `name`, `confirm: str`; reject wrong confirmation string; reject if season not ACTIVE; reject if division already CANCELLED; call `cancel_division`; post cancellation notice to division's `forecast_channel_id` (no role mention) via `output_router`
- [X] T027 [P] [US6] Add `/round cancel` as `@round.command(name="cancel")` in `src/cogs/season_cog.py` — parameters: `division_name`, `round_number`, `confirm: str`; reject wrong confirmation string; reject if season not ACTIVE; reject if round already CANCELLED; permitted if phases have started; call `cancel_round`; post cancellation notice to division's `forecast_channel_id` (no role mention) via `output_router`

---

## Phase 9: User Story 7 — Full Season Cancellation

**Story goal**: `/season cancel` (server-admin only) deletes the season entirely after confirming and posting to all active division forecast channels.

**Independent test**: Run `/season cancel confirm:CONFIRM` as server admin; verify both active division forecast channels receive notices (no role pings) and `/season setup` succeeds immediately after.

- [X] T028 [US7] Add `SeasonService.delete_season(season_id: int)` in `src/services/season_service.py` — FK-safe cascade delete in order: `forecast_messages` → `phase_results` → `sessions` → `rounds` → `divisions` → `seasons` (mirrors the ordering in `reset_service.py`)
- [X] T029 [US7] Add `/season cancel` as `@season.command(name="cancel")` in `src/cogs/season_cog.py` — decorate with `@admin_only` (from `src/utils/channel_guard.py`); parameter: `confirm: str`; reject wrong confirmation; reject if season not ACTIVE; iterate ACTIVE divisions and post notice to each `forecast_channel_id` (no role mention); call `delete_season`

---

## Phase 10: User Story 8 — Division Rename During Setup

**Story goal**: `/division rename` changes a division's name in setup mode with no other side effects.

**Independent test**: Create division "Pro"; rename to "Pro-Am"; verify division list shows "Pro-Am" and its rounds are unchanged.

- [X] T030 [US8] Add `SeasonService.rename_division(division_id: int, new_name: str)` in `src/services/season_service.py` — single UPDATE on `divisions.name`
- [X] T031 [US8] Add `/division rename` as `@division.command(name="rename")` in `src/cogs/season_cog.py` — parameters: `current_name`, `new_name`; reject if season not SETUP; reject if `current_name` not found; reject if `new_name` already in use; call `rename_division`; respond with `format_division_list`

---

## Phase 11: User Story 9 — Test Mode Restricted to Server Administrators

**Story goal**: All three `/test-mode` subcommands require `manage_guild` permission; interaction-role holders without admin rights are rejected.

**Independent test**: Invoke `/test-mode toggle` as a non-admin interaction-role holder — get permission error. Invoke as server admin — succeeds.

- [X] T032 [US9] In `src/cogs/test_mode_cog.py`, replace the `@channel_guard` decorator on `toggle`, `advance`, and `review` subcommands with `@admin_only` (already defined in `src/utils/channel_guard.py`); `default_permissions=None` and `guild_only=True` on the group remain unchanged

---

## Final Phase: Polish & Cross-Cutting Concerns

**Purpose**: Migrate the remaining stranded command, document everything for users, and verify the full test suite passes.

- [X] T033 Migrate `/round amend` from `src/cogs/amendment_cog.py` into the `round` group in `src/cogs/season_cog.py` as `@round.command(name="amend")`; retire `AmendmentCog` (remove from bot.py cog loading or leave as empty stub)
- [X] T034 [P] Rewrite the **Slash Commands** section and **Season Setup Workflow** section of `README.md` — document all five command groups (`/bot-init`, `/bot-reset`, `/season`, `/division`, `/round`, `/track`, `/test-mode`), their subcommands with parameter tables, removed parameters (`start_date`, `num_divisions`, `round_number`), access levels, and season lifecycle
- [X] T035 Run `pytest` from the repository root; fix any regressions caused by the command migration (`season_setup` → `season setup`, `round_add` → `round add`, `round_amend` → `round amend`) and service signature changes (`create_season` without `start_date`)

---

## Dependencies

```
T001 → T002, T003 (schema must exist before model fields matter in tests)
T004, T005 → T010, T012, T013 (service methods used by cog changes)
T006, T007 → T014, T015, T016, T018, T021, T022, T026, T027, T031 (helpers used in responses)
T008 → T014, T017, T018, T019, T021, T024, T026, T030, T031 (division group must exist)
T009, T010 → T012 (season group and /season setup must be migrated before /round add structure lands)
T011 → T012 (DuplicateRoundView must be removed before reworking round_add)
T012 → T015, T022, T027 (round group must exist)
T017 → T018 (service before cog)
T019, T020 → T021, T022 (services before cogs)
T023 → T026, T027 (scheduler skip must land alongside cancel commands)
T024, T025 → T026, T027 (services before cogs)
T028 → T029 (service before cog)
T030 → T031 (service before cog)
T033 → T035 (migration must happen before final test run)
```

## Parallel execution examples

**US4 (division duplication)**: T017 and overall US5 service tasks T019+T020 can run in parallel because they touch different methods in season_service.py.

**US5 (deletion)**: T019/T020 can run in parallel (different methods); T021/T022 can run in parallel after T019/T020 (different commands in same file, both are additions).

**US6 (cancellation)**: T024/T025 can run in parallel (different methods); T026/T027 can run in parallel after T024/T025 (different commands).

**US7 + US8**: T028 (delete_season) and T030 (rename_division) touch different methods and can run in parallel.

**Final phase**: T033 and T034 touch different files (`season_cog.py` vs `README.md`) and can run in parallel.

## Implementation strategy

**MVP scope** (delivers testable value immediately): US1 + US2 + US9 (T001–T016 + T032). This removes the two most confusing parameters from everyday use and locks down test-mode access.

**Full scope order**: Follow phase sequence T001 → T035. Each phase is independently testable using the quickstart scenarios in `specs/010-command-streamlining/quickstart.md`.

| Task count | Value |
|---|---|
| 35 total tasks | — |
| US1: 2 tasks | Simplified season setup |
| US2: 3 tasks | Auto-derived round numbers |
| US3: 3 tasks | Post-modification feedback |
| US4: 2 tasks | Division duplication |
| US5: 4 tasks | Division/round deletion |
| US6: 5 tasks | In-season cancellation |
| US7: 2 tasks | Full season cancellation |
| US8: 2 tasks | Division rename |
| US9: 1 task | Test mode restriction |
| Foundational: 8 tasks | Shared infrastructure |
| Polish: 3 tasks | Migration, README, test run |
