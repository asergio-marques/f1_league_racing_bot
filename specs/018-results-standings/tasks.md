# Tasks: Results & Standings — Module Registration and Channel Setup (018)

**Input**: Design documents from `specs/018-results-standings/`
**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | quickstart.md ✅ | contracts/ ✅

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to ([US1]–[US5])

---

## Phase 1: Setup

**Purpose**: Migration — prerequisite for all service and cog work.

- [ ] T001 Create `src/db/migrations/016_results_standings_channels.sql` with three tables per data-model.md: `results_module_config` (server_id PK, module_enabled INTEGER DEFAULT 0), `division_results_config` (division_id PK, results_channel_id, standings_channel_id, reserves_in_standings INTEGER DEFAULT 1), `season_points_links` (id PK AUTOINCREMENT, season_id FK, config_name TEXT, UNIQUE season_id+config_name)

**Checkpoint**: Migration file in place — model and service changes can begin.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data and service layer that all user story cog implementations depend on.

**⚠️ CRITICAL**: Phase 3–6 cog changes cannot be completed without the service APIs from this phase.

- [ ] T002 [P] Add `results_channel_id: int | None = None` and `standings_channel_id: int | None = None` optional fields to `Division` dataclass in `src/models/division.py`
- [ ] T003 [P] Add `is_results_enabled(server_id: int) -> bool` to `src/services/module_service.py`: SELECT module_enabled FROM results_module_config; return False if row absent
- [ ] T004 [P] Add `set_results_enabled(server_id: int, value: bool) -> None` to `src/services/module_service.py`: INSERT OR REPLACE into results_module_config (upsert; no separate row-creation step)
- [ ] T005 Add `get_season_for_server(server_id: int) -> Season | None` to `src/services/season_service.py`: SELECT from seasons WHERE server_id = ? ORDER BY id DESC LIMIT 1 (any status — used by channel assignment commands)
- [ ] T006 Add `set_division_forecast_channel(division_id: int, channel_id: int | None) -> int | None` to `src/services/season_service.py`: UPDATE divisions.forecast_channel_id, return the previous value (fetched before update for idempotency checks and audit)
- [ ] T007 Add `set_division_results_channel(division_id: int, channel_id: int | None) -> int | None` to `src/services/season_service.py`: INSERT INTO division_results_config ON CONFLICT(division_id) DO UPDATE SET results_channel_id = excluded.results_channel_id; return previous value
- [ ] T008 Add `set_division_standings_channel(division_id: int, channel_id: int | None) -> int | None` to `src/services/season_service.py`: same upsert pattern targeting standings_channel_id; return previous value
- [ ] T009 Add `get_divisions_with_results_config(season_id: int) -> list[Division]` to `src/services/season_service.py`: SELECT d.*, drc.results_channel_id, drc.standings_channel_id FROM divisions d LEFT JOIN division_results_config drc ON drc.division_id = d.id WHERE d.season_id = ?; populate Division.results_channel_id and Division.standings_channel_id fields
- [ ] T010 Change `forecast_channel_id` parameter from `int` to `int | None = None` in `add_division` in `src/services/season_service.py`
- [ ] T011 Change `forecast_channel_id` parameter from `int` to `int | None = None` in `duplicate_division` in `src/services/season_service.py`

**Checkpoint**: Service layer complete — all cog tasks for US1–US4 can now proceed.

---

## Phase 3: User Story 1 — Server Admin Enables/Disables the R&S Module (Priority: P1) 🎯 MVP

**Goal**: A server admin can enable and disable the Results & Standings module via `/module enable results` and `/module disable results`. Enable is blocked if an ACTIVE season exists. Disable is unrestricted.

**Independent Test**: Enable R&S on a server with no active season → module reported enabled. Attempt enable with an ACTIVE season → blocked with clear error. Disable → module reported disabled. Attempt double-enable → `⚠️` message, no state change.

- [ ] T012 [US1] Add `app_commands.Choice(name="results", value="results")` to `_MODULE_CHOICES` list and add `elif module_name.value == "results":` dispatch branches in both `enable` and `disable` handlers in `src/cogs/module_cog.py`
- [ ] T013 [US1] Implement `_enable_results(interaction, server_id)` in `src/cogs/module_cog.py`: (1) guard already-enabled via `is_results_enabled`; (2) block if `get_active_season` returns a season; (3) defer; (4) INSERT OR REPLACE into results_module_config with module_enabled=1 and INSERT audit entry (`MODULE_ENABLE`, new_value=`{"module":"results"}`) in one transaction; (5) `post_log` + followup `✅ Results & Standings module enabled.`
- [ ] T014 [US1] Implement `_disable_results(interaction, server_id)` in `src/cogs/module_cog.py`: (1) guard already-disabled via `is_results_enabled`; (2) defer; (3) INSERT OR REPLACE with module_enabled=0 and INSERT audit entry (`MODULE_DISABLE`, old_value=`{"module":"results"}`) in one transaction; (4) `post_log` + followup `✅ Results & Standings module disabled.`

**Checkpoint**: `/module enable results` and `/module disable results` fully functional and independently testable.

---

## Phase 4: User Story 2 — Trusted Admin Assigns Weather Forecast Channel (Priority: P1)

**Goal**: The weather forecast channel is no longer set during division creation. A Tier-2 admin uses `/division weather-channel` instead. Existing stored `forecast_channel_id` values are not cleared.

**Independent Test**: `/division add name:Div1 role:@R` succeeds with no channel parameter and creates the division with `forecast_channel_id = NULL`. `/division weather-channel name:Div1 channel:#some` stores the channel ID. Running the command again with the same channel returns `ℹ️` (no state change). Running with a different channel replaces the value.

- [ ] T015 [US2] Remove `forecast_channel` parameter from `/division add` in `src/cogs/season_cog.py`: delete the `@app_commands.describe` forecast_channel entry, delete the `forecast_channel: discord.TextChannel | None = None` parameter, delete both weather mutual-exclusivity guard blocks (both `if weather_enabled` checks), update `PendingDivision(...)` to `channel_id=None`, remove `channel_mention` line from confirmation message
- [ ] T016 [US2] Remove `forecast_channel` parameter from `/division duplicate` in `src/cogs/season_cog.py`: same removals as T015 for the duplicate command's describe/parameter/guard blocks; update `duplicate_division(...)` call to pass `forecast_channel_id=None`
- [ ] T017 [US2] Add private `_set_division_channel(interaction, name, channel, channel_type)` helper to `SeasonCog` in `src/cogs/season_cog.py`: (1) resolve season via `get_season_for_server`; (2) find division by name case-insensitively via `get_divisions`; (3) dispatch to `set_division_forecast_channel`/`set_division_results_channel`/`set_division_standings_channel` based on `channel_type`; (4) if returned old_id matches new channel.id, respond `ℹ️` and return; (5) INSERT audit entry (`DIVISION_CHANNEL_SET`, old_value/new_value JSON with channel_type and channel_id); (6) respond `✅ {type_label} channel for **{name}** set to {channel.mention}.`
- [ ] T018 [US2] Add `/division weather-channel` command to `src/cogs/season_cog.py` with `@channel_guard` (no `@admin_only`), parameters `name: str` and `channel: discord.TextChannel`, that calls `await self._set_division_channel(interaction, name, channel, "weather")`

**Checkpoint**: `/division add` no longer accepts a weather channel. `/division weather-channel` assigns it independently. Both independently testable.

---

## Phase 5: User Story 3 — Trusted Admin Assigns Results and Standings Channels (Priority: P1)

**Goal**: `/division results-channel` and `/division standings-channel` each store their respective channel IDs per division independently. Changing one does not affect the other.

**Independent Test**: Assign both channels to a division. Verify each is stored. Re-assign results channel only; confirm standings channel unchanged. Re-assign standings channel only; confirm results channel unchanged.

- [ ] T019 [US3] Add `/division results-channel` command to `src/cogs/season_cog.py` with `@channel_guard`, parameters `name: str` and `channel: discord.TextChannel`, that calls `await self._set_division_channel(interaction, name, channel, "results")`
- [ ] T020 [US3] Add `/division standings-channel` command to `src/cogs/season_cog.py` with `@channel_guard`, parameters `name: str` and `channel: discord.TextChannel`, that calls `await self._set_division_channel(interaction, name, channel, "standings")`

**Checkpoint**: All three channel assignment commands functional. Each stores its channel independently. Audit entries recorded for all three.

---

## Phase 6: User Story 4 — Season Approval Guarded by Channel Prerequisites (Priority: P2)

**Goal**: `/season review` → Approve is blocked if the weather module is enabled and any division lacks a forecast channel, or if the R&S module is enabled and any division lacks a results/standings channel or no points config is attached to the season.

**Independent Test**: Enable weather + leave one division without forecast channel → approval rejected naming that division. Fix it → weather gate passes. Enable R&S + leave channels and points config unconfigured → approval listing all gaps. Fix all → approval succeeds.

- [ ] T021 [US4] Insert weather channel prerequisite gate into `SeasonCog._do_approve` in `src/cogs/season_cog.py`, immediately after the `validate_division_tiers` call and before the `schedule_all_rounds` call: check `is_weather_enabled`; if True, find divisions where `forecast_channel_id` is falsy; if any, build named list and respond with `❌ Season cannot be approved — the following divisions are missing a weather forecast channel: {names}.` using `interaction.response.send_message` or `interaction.followup.send` per existing `is_done()` pattern; return
- [ ] T022 [US4] Insert R&S prerequisite gate into `SeasonCog._do_approve` in `src/cogs/season_cog.py`, immediately after T021's gate: check `is_results_enabled`; if True, call `get_divisions_with_results_config`; collect errors for each division missing `results_channel_id` and for each missing `standings_channel_id`; also SELECT COUNT(*) from season_points_links WHERE season_id = cfg.season_id and append "no points configuration is attached to this season" if count is 0; if any errors, respond with bullet-list `❌` message and return

**Checkpoint**: Season approval correctly blocks on all configured module prerequisites. Both gates at full P2 priority are operational.

---

## Phase 7: User Story 5 — Weather Enable Guard on Active Season (Priority: P2)

**Goal**: `/module enable weather` is blocked if the server has an ACTIVE season and any division in that season lacks a weather forecast channel.

> **Note**: This behaviour is already fully implemented in the existing `_enable_weather` method in `src/cogs/module_cog.py` (guard checks `division.forecast_channel_id` for all active-season divisions). No implementation changes are required — this user story is satisfied automatically by the combination of the existing guard plus the foundational service changes in Phase 2. Verification is via the unit tests in Phase 8.

**Checkpoint**: No new code required. Existing guard handles US5. Covered by tests in Phase 8.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T023 [P] Create `tests/unit/test_results_module_service.py` with in-memory aiosqlite DB tests (same pattern as existing unit tests): `test_is_results_enabled_default_false` (no row → False), `test_set_results_enabled_true` (upsert → re-read True), `test_set_results_enabled_false` (true then false → False), `test_set_results_enabled_idempotent` (double-enable does not error)
- [ ] T024 [P] Create `tests/unit/test_season_approval_gates.py` with in-memory aiosqlite DB tests: `test_approve_no_gates_pass` (neither module enabled → no gate fires), `test_approve_weather_gate_blocks` (weather enabled + missing forecast_channel → error naming division), `test_approve_weather_gate_passes` (all channels set → gate does not block), `test_approve_rs_gate_blocks_missing_results` (R&S enabled + missing results_channel → error), `test_approve_rs_gate_blocks_missing_standings` (missing standings_channel → error), `test_approve_rs_gate_blocks_no_points_config` (all channels set + no season_points_links row → error), `test_approve_rs_gate_passes` (all channels set + one points link row → no block), `test_weather_enable_guard_active_season` (US5: active season + missing forecast_channel → enable blocked naming division)

---

## Dependencies & Execution Order

```
T001 (migration)
  └── T002, T003, T004 [parallel — different files]
          └── T005–T011 (season_service methods — sequential, same file)
                  └── T012–T014 (US1 — module_cog; parallel with T015–T022 once T003/T004 done)
                  └── T015–T018 (US2 — season_cog)
                          └── T019–T020 (US3 — season_cog; depends on T017 helper)
                  └── T021–T022 (US4 — season_cog; depends on T009 + T003/T004)
  └── T023, T024 [parallel — new test files; can start after T001+T002+T003+T004]
```

### Parallel Execution Examples

**All of Phase 2** (after T001):
- Developer A: T002 (division model) + T003 + T004 (module service)
- Developer B: T005–T011 (season service methods)

**US1 and US2/US3 in parallel** (after Phase 2):
- Developer A: T012 → T013 → T014 (US1, module_cog.py)
- Developer B: T015 → T016 → T017 → T018 → T019 → T020 (US2+US3, season_cog.py)

**US4 gates** depend on T017 (helper) and T009 (get_divisions_with_results_config) — implement after US3.

**Tests** (T023, T024) can be written any time after Phase 2 is complete; they do not depend on cog changes.

---

## Implementation Strategy

**MVP scope** (deliverable after Phase 3): The R&S module can be enabled and disabled by a server admin. All service infrastructure is in place. Season approval and channel decoupling are not yet wired up but the foundation is solid.

**Full P1 scope** (Phases 1–5): Module enable/disable, full channel decoupling from division-add, all three channel assignment commands working. Season setup flow is clean. Still missing: approval gates (P2).

**Full P2 scope** (Phases 1–7): All approval gates enforced. Weather enable guard verified. Module is ready for the next increment (points configuration feature).

---

## Summary

| Phase | User Story | Tasks | Notes |
|-------|-----------|-------|-------|
| 1 — Setup | — | T001 | Migration only |
| 2 — Foundational | — | T002–T011 | Model + service layer |
| 3 — US1 | R&S module enable/disable | T012–T014 | P1; can parallel with Phase 4–5 |
| 4 — US2 | Weather channel decoupling | T015–T018 | P1; depends on T005–T007, T010–T011 |
| 5 — US3 | Results/standings channels | T019–T020 | P1; depends on T017 (helper) |
| 6 — US4 | Approval gates | T021–T022 | P2; depends on T009, T003, T004 |
| 7 — US5 | Weather enable guard | — | P2; already implemented |
| 8 — Polish | Tests | T023–T024 | After Phase 2 complete |

**Total tasks**: 24 | **Parallelizable**: T002, T003, T004, T023, T024
