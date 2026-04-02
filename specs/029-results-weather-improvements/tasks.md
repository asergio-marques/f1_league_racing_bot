# Tasks: Results Resubmission & Weather Phase Configurability

**Input**: Design documents from `/specs/029-results-weather-improvements/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no incomplete dependencies)
- **[Story]**: User story this task belongs to (US1–US3)
- Exact file paths included in every description

---

## Phase 1: Setup

**Purpose**: DB schema foundation required before any weather config code can run.

- [X] T001 Create migration `src/db/migrations/028_weather_pipeline_config.sql` — `CREATE TABLE IF NOT EXISTS weather_pipeline_config (server_id INTEGER PRIMARY KEY REFERENCES server_configs(server_id) ON DELETE CASCADE, phase_1_days INTEGER NOT NULL DEFAULT 5, phase_2_days INTEGER NOT NULL DEFAULT 2, phase_3_hours INTEGER NOT NULL DEFAULT 2)`

---

## Phase 2: Foundational (Blocking Prerequisites for US2 & US3)

**Purpose**: Model, scheduler signature change, and base service reader shared by both weather stories. Must be complete before Phase 4 begins.

**⚠️ CRITICAL**: US2 and US3 cannot begin until T002–T004 are done.

- [X] T002 [P] Create `WeatherPipelineConfig` dataclass in `src/models/weather_config.py` — fields: `server_id: int`, `phase_1_days: int = 5`, `phase_2_days: int = 2`, `phase_3_hours: int = 2`
- [X] T003 [P] Update `SchedulerService.schedule_round()` in `src/services/scheduler_service.py` — add `*, phase_1_days: int = 5, phase_2_days: int = 2, phase_3_hours: int = 2` kwargs; replace the hardcoded `timedelta(days=5)`, `timedelta(days=2)`, `timedelta(hours=2)` in the `horizons` dict with `timedelta(days=phase_1_days)`, `timedelta(days=phase_2_days)`, `timedelta(hours=phase_3_hours)`; also update `schedule_all_rounds()` to accept and thread the same three kwargs to each `schedule_round()` call
- [X] T004 Create `src/services/weather_config_service.py` with a single async function `get_weather_pipeline_config(db_path: str, server_id: int) -> WeatherPipelineConfig` — executes `SELECT phase_1_days, phase_2_days, phase_3_hours FROM weather_pipeline_config WHERE server_id = ?`; returns a `WeatherPipelineConfig` with the stored values, or a default-valued instance (`phase_1_days=5, phase_2_days=2, phase_3_hours=2`) if no row exists

**Checkpoint**: Migration, model, scheduler kwargs, and config reader are all ready. US1 and US2/US3 can now proceed independently.

---

## Phase 3: User Story 1 — Results Hotfix Resubmission (Priority: P1) 🎯 MVP

**Goal**: A tier-2 admin can press "🔄 Resubmit Initial Results" during the penalty wizard to discard any staged penalties and re-enter the session's results from scratch. Amended provisional results are posted with "(amended)" in the title.

**Independent Test**: Submit results deliberately wrong → verify provisional results posted → stage a penalty in the wizard → press "Resubmit Initial Results" → re-enter corrected results → confirm: (a) "Provisional Results (amended)" appears in results channel, (b) standings recomputed, (c) no staged penalty in the record, (d) two audit log entries (`RESULTS_RESUBMISSION_STAGED_DISCARD`, `RESULTS_RESUBMISSION`) exist.

- [X] T005 [P] [US1] In `src/services/penalty_wizard.py`, add the constant `_CID_RESUBMIT = "pw_resubmit"` alongside the existing `_CID_*` constants; add a `🔄 Resubmit Initial Results` `discord.ui.Button` (style=`ButtonStyle.danger`, `custom_id=_CID_RESUBMIT`, row=0) to `PenaltyReviewView` — callback defers as ephemeral; if `state is None` sends the standard restart ephemeral notice (matching existing pattern); otherwise calls `await enter_resubmit_flow(interaction, state)`
- [X] T006 [P] [US1] In `src/services/result_submission_service.py`, add `is_resubmission: bool = False` keyword parameter to `enter_penalty_state()` — when `True`, pass `label="Provisional Results (amended)"` (instead of `"Provisional Results"`) to both `results_post_service.post_round_results(...)` and `results_post_service.post_standings(...)`
- [X] T007 [US1] In `src/services/result_submission_service.py`, implement `async def enter_resubmit_flow(interaction: discord.Interaction, state: PenaltyReviewState) -> None` — (1) write audit log entry `RESULTS_RESUBMISSION_STAGED_DISCARD` with actor identity, `state.round_id`, and count/IDs of discarded staged entries; (2) clear `state.staged` in-place; (3) execute `UPDATE driver_session_results SET is_superseded = 1 WHERE round_id = ?` for all sessions of `state.round_id`; (4) execute `DELETE FROM session_results WHERE round_id = ?`; (5) execute `UPDATE round_submission_channels SET in_penalty_review = 0, results_posted = 0 WHERE round_id = ?`; (6) post a `"⚠️ Results resubmission started. Previous provisional results will be replaced."` notice to the submission channel; (7) re-invoke the first-session collection prompt using the same entry path as the original submission start (so the collection wizard restarts from session 1); on re-completion `enter_penalty_state` is called with `is_resubmission=True`; (8) write audit log entry `RESULTS_RESUBMISSION` with actor identity and round context

**Checkpoint**: User Story 1 is fully functional and independently testable. US2/US3 work can proceed in parallel.

---

## Phase 4: User Story 2 — Configure Weather Phase Deadlines (Priority: P2)

**Goal**: League managers can run `/weather config phase-1-deadline`, `phase-2-deadline`, and `phase-3-deadline` to store per-server deadline values that are used when scheduling round weather phases, subject to a module-enabled gate and an active-season gate.

**Independent Test**: Enable the weather module on a test server with no active season. Run `/weather config phase-1-deadline 7` → confirm success response and persistence across restart. Confirm that the next call to `schedule_round()` uses `phase_1_days=7`. Run any deadline command while a season is ACTIVE → confirm rejection with correct error.

- [X] T008 [P] [US2] In `src/services/weather_config_service.py`, add three async upsert functions: `set_phase_1_days(db_path, server_id, days)`, `set_phase_2_days(db_path, server_id, days)`, `set_phase_3_hours(db_path, server_id, hours)` — each executes `INSERT INTO weather_pipeline_config (...) VALUES (...) ON CONFLICT(server_id) DO UPDATE SET <column> = excluded.<column>`; returns the updated `WeatherPipelineConfig`; does **not** validate ordering (ordering validation added in US3)
- [X] T009 [P] [US2] In `src/cogs/module_cog.py`, update `_catchup_and_schedule_weather()` — after fetching the round list, call `await weather_config_service.get_weather_pipeline_config(self.bot.db_path, server_id)` and pass `phase_1_days`, `phase_2_days`, `phase_3_hours` as kwargs to each `self.bot.scheduler_service.schedule_round(rnd, ...)` call
- [X] T010 [P] [US2] In `src/cogs/season_cog.py`, update the season-activation path — before calling `self.bot.scheduler_service.schedule_all_rounds(all_rounds)`, fetch `WeatherPipelineConfig` via `weather_config_service.get_weather_pipeline_config` and pass the three phase kwargs to `schedule_all_rounds()`
- [X] T011 [US2] Create `src/cogs/weather_cog.py` with `WeatherCog(commands.Cog)` — define `weather = app_commands.Group(name="weather", description="Weather module commands")` and a nested `config_group = app_commands.Group(name="config", parent=weather, description="Configure weather pipeline settings")`; implement three `@config_group.command(...)` handlers (`phase_1_deadline(days: int)`, `phase_2_deadline(days: int)`, `phase_3_deadline(hours: int)`); each handler: (a) checks weather module enabled via `bot.module_service` → ephemeral error if not; (b) checks no ACTIVE season via `bot.season_service.get_active_season(server_id)` → ephemeral rejection if active; (c) validates `days >= 1` / `hours >= 1`; (d) calls the corresponding `set_phase_*` function; (e) writes audit log entry with change type from contracts (`WEATHER_CONFIG_PHASE1_DEADLINE` etc.), old value, new value, actor; (f) responds with success message including all three current values (ordering error branch added in US3)
- [X] T012 [US2] In `src/bot.py`, load `WeatherCog` in `setup_hook` (alongside existing cog additions) with `await self.add_cog(WeatherCog(self))`

**Checkpoint**: All three deadline commands are live, persist across restarts, and the scheduler uses the stored values. US3 ordering validation can now be layered on.

---

## Phase 5: User Story 3 — Phase Deadline Ordering Validation (Priority: P2)

**Goal**: Any deadline command that would produce a configuration violating `(P1_days × 24) > (P2_days × 24) > P3_hours` is rejected before any state change, with an error identifying the conflicting current value.

**Independent Test**: With defaults (P1=5d, P2=2d, P3=2h): run `/weather config phase-1-deadline 1` → rejected citing P2 conflict; run `/weather config phase-2-deadline 6` → rejected citing P1 conflict; run `/weather config phase-3-deadline 72` → rejected citing P2 conflict. After each rejection, confirm config is unchanged (persistence check).

- [X] T013 [US3] In `src/services/weather_config_service.py`, add `validate_ordering(p1_days: int, p2_days: int, p3_hours: int) -> str | None` — returns `None` if `(p1_days * 24) > (p2_days * 24) > p3_hours` (strict), otherwise returns a descriptive error string identifying which constraint is violated (e.g. `"Phase 1 (Xd) must be greater than Phase 2 (Yd)"`)
- [X] T014 [US3] In `src/services/weather_config_service.py`, integrate `validate_ordering` into each set method — before the upsert, fetch the current config via `get_weather_pipeline_config`, substitute the candidate new value, call `validate_ordering` with the resulting trio; if an error string is returned, do **not** execute the upsert and return the error string to the caller instead of the updated config
- [X] T015 [US3] In `src/cogs/weather_cog.py`, update each `phase-N-deadline` command handler to handle an ordering-error return from the set method — send an ephemeral error response with the exact message format from `contracts/commands.md` (e.g. `"❌ Phase 2 deadline (6d) must be less than the current Phase 1 deadline (5d). ..."`)

**Checkpoint**: All three user stories are fully functional and independently testable. The full feature is complete.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final integration checks and housekeeping.

- [X] T016 Verify migration file is named `028_weather_pipeline_config.sql` (no gaps relative to `027_season_signup_flow.sql`) and bot starts cleanly with `python -m pytest tests/ -v`; confirm `WeatherCog` appears in the cog load log on startup

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 (T001) — blocks US2 and US3 only
- **Phase 3 (US1)**: Depends on **nothing in Phase 2** — can start in parallel with Phase 2 immediately after Phase 1
- **Phase 4 (US2)**: Depends on Phase 2 completion (T002, T003, T004)
- **Phase 5 (US3)**: Depends on Phase 4 completion (T008, T011 must exist before ordering validation is wired in)
- **Phase 6 (Polish)**: Depends on all phases complete

### User Story Dependencies

| Story | Depends on | Independent from |
|---|---|---|
| US1 (Results Resubmission) | Phase 1 only | US2, US3, Phase 2 entirely |
| US2 (Configure Deadlines) | Phase 2 (T001–T004) | US1 |
| US3 (Ordering Validation) | US2 phase complete (T008 for set methods, T011 for cog) | US1 |

### Within Phase 3 (US1)

- T005 (penalty_wizard.py) and T006 (result_submission_service.py) can be written in parallel — different files
- T007 (result_submission_service.py) follows T006 — same file, calls `enter_penalty_state(is_resubmission=True)`

### Within Phase 4 (US2)

- T008, T009, T010 can all be written in parallel — all different files, all depend only on T002–T004
- T011 (weather_cog.py) follows T008 (needs set methods to call)
- T012 (bot.py) follows T011 (needs the WeatherCog class to exist)

### Within Phase 5 (US3)

- T013 → T014 → T015 are sequential (same file for T013+T014; T015 depends on T014's error return)

---

## Parallel Execution Examples

### Phase 3 (US1) — Two tracks

```
Track A: T005 (penalty_wizard.py button)
Track B: T006 → T007 (result_submission_service.py is_resubmission + flow)
Merge: both complete → US1 is independently testable
```

### Phase 4 (US2) — Three parallel + sequential tail

```
Track A: T008 (weather_config_service.py set methods)
Track B: T009 (module_cog.py caller update)
Track C: T010 (season_cog.py caller update)
Sequential tail: T011 (weather_cog.py, needs T008) → T012 (bot.py)
```

---

## Implementation Strategy

**MVP (User Story 1 only)**: Complete Phase 1 + Phase 3 (T001, T005, T006, T007) for a fully deliverable results hotfix resubmission capability with no weather work required.

**Full feature**: Add Phase 2 + Phase 4 + Phase 5 for complete weather configuration capability. Phase 2 can be worked in parallel with Phase 3.

**Suggested order for a single developer**:
1. T001 (migration — fast)
2. T002, T003 in parallel (model + scheduler — fast, different files)
3. T004 (config reader)
4. T005, T006 in parallel (US1 prep — different files)
5. T007 (US1 resubmit flow — core logic)
6. T008, T009, T010 in parallel (US2 prep)
7. T011 (weather cog)
8. T012 (register cog)
9. T013 → T014 → T015 (US3 validation)
10. T016 (polish/smoke test)
