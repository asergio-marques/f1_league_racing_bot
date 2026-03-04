# Tasks: F1 League Weather Randomizer Bot — Core System

**Input**: Design documents from `specs/001-league-weather-bot/`
**Prerequisites**: plan.md ✅, spec.md ✅

**Organization**: Tasks are grouped by user story to enable independent implementation and
testing of each story. Stories are ordered by spec priority (P1 → P4); foundational
infrastructure is separated into Phase 2 as it blocks all stories equally.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies within phase)
- **[Story]**: Which user story this task belongs to ([US1]–[US4])
- Exact file paths are included in every task description

---

## Phase 1: Setup

**Purpose**: Project scaffolding and dependency installation

- [X] T001 Create directory structure per plan.md: `src/models/`, `src/db/migrations/`, `src/services/`, `src/cogs/`, `src/utils/`, `tests/unit/`, `tests/integration/` with `__init__.py` in each Python package
- [X] T002 Create `requirements.txt` listing: `discord.py`, `apscheduler>=3.10`, `aiosqlite>=0.19`, `pytest>=7`, `pytest-asyncio>=0.23`, `sqlalchemy` (required by APScheduler SQLAlchemyJobStore)
- [X] T003 [P] Create `.env.example` with `BOT_TOKEN=` and `DB_PATH=bot.db` placeholder entries
- [X] T004 [P] Create `src/bot.py` entry-point skeleton: load `.env`, instantiate `commands.Bot` with `intents`, stub cog-loading calls, create and start `AsyncIOScheduler`, run bot

**Checkpoint**: `src/` and `tests/` trees exist; `pip install -r requirements.txt` succeeds

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Database layer, all data models, and shared utilities that every user story
depends on. No user story work may begin until this phase is complete.

**⚠️ CRITICAL**: All stories share these components. Complete before any Phase 3+ work.

- [X] T005 Create `src/db/migrations/001_initial.sql`: full schema for all 8 tables (`server_configs`, `seasons`, `divisions`, `rounds`, `sessions`, `phase_results`, `audit_entries`, `schema_migrations`) with foreign keys, indexes on `(server_id)`, `(season_id)`, `(division_id)`, `(round_id)`
- [X] T006 Create `src/db/database.py`: async `get_connection()` context manager (sets `PRAGMA foreign_keys = ON`), `run_migrations()` function that reads `schema_migrations` and applies any unapplied `.sql` files from `migrations/` in order; call `run_migrations()` on bot startup from `src/bot.py`
- [X] T007 [P] Create `src/models/track.py`: `TRACKS` frozen dict mapping the 27 circuit names to their `Btrack` fractional base factor (e.g., `"Bahrain": 0.05`); `get_btrack(name: str) -> float` raising `ValueError` for unknown tracks
- [X] T008 [P] Create `src/models/server_config.py`: `ServerConfig` dataclass with fields `server_id: int`, `interaction_role_id: int`, `interaction_channel_id: int`, `log_channel_id: int`
- [X] T009 [P] Create `src/models/season.py`: `SeasonStatus` enum (`SETUP`, `ACTIVE`, `COMPLETED`); `Season` dataclass with `id`, `server_id`, `start_date`, `status: SeasonStatus`
- [X] T010 [P] Create `src/models/division.py`: `Division` dataclass with `id`, `season_id`, `name`, `mention_role_id: int`, `forecast_channel_id: int`, `race_day: int` (0=Mon), `race_time: str` (HH:MM UTC)
- [X] T011 [P] Create `src/models/round.py`: `RoundFormat` enum (`NORMAL`, `SPRINT`, `MYSTERY`, `ENDURANCE`); `Round` dataclass with `id`, `division_id`, `round_number`, `format: RoundFormat`, `track_name: str | None`, `scheduled_at: datetime`, `phase1_done: bool`, `phase2_done: bool`, `phase3_done: bool`
- [X] T012 [P] Create `src/models/session.py`: `SessionType` enum (`SHORT_QUALIFYING`, `SHORT_SPRINT_QUALIFYING`, `SHORT_FEATURE_QUALIFYING`, `LONG_RACE`, `LONG_FEATURE_RACE`, `LONG_SPRINT_RACE`, `FULL_QUALIFYING`, `FULL_RACE`); `MAX_SLOTS: dict[SessionType, int]` constant; `SESSIONS_BY_FORMAT: dict[RoundFormat, list[SessionType]]` constant; `Session` dataclass with `id`, `round_id`, `session_type`, `phase2_slot_type: str | None` (`"rain"`, `"mixed"`, `"sunny"`), `phase3_slots: list[str] | None`
- [X] T013 [P] Create `src/models/phase_result.py`: `PhaseStatus` enum (`ACTIVE`, `INVALIDATED`); `PhaseResult` dataclass with `id`, `round_id`, `phase_number: int`, `payload: dict` (full input+output snapshot), `status: PhaseStatus`, `created_at: datetime`
- [X] T014 [P] Create `src/models/audit_entry.py`: `AuditEntry` dataclass with `id`, `server_id`, `actor_id: int`, `actor_name: str`, `division_id: int | None`, `change_type: str`, `old_value: str`, `new_value: str`, `timestamp: datetime`
- [X] T015 Create `src/utils/math_utils.py`: `compute_rpc(btrack, rand1, rand2) -> float` (rounds to 2 dp, clamps to [0.0, 1.0]); `compute_ir(rpc) -> int` (`floor((1000 * rpc * (1 + rpc)**2) / 5)`); `compute_im(rpc, ir) -> int`; `compute_is(im, ir) -> int`; all Phase 3 weight functions per weather type and session type; `clamp_weight(v) -> float` (`max(0.0, v)`)
- [X] T016 [P] Create `src/utils/message_builder.py`: `phase1_message(division_role_id, track, rpc_pct) -> str`; `phase2_message(division_role_id, track, session_slots: list[tuple[str,str]]) -> str`; `phase3_message(division_role_id, track, session_weather: list[tuple[str, list[str]]]) -> str` (natural-language slot rendering); `invalidation_message(track) -> str`
- [X] T017 Create `src/utils/channel_guard.py`: `channel_guard` decorator for `app_commands` functions that silently ignores commands outside the configured interaction channel and raises ephemeral permission error for non–interaction-role users; reads `ServerConfig` via injected `config_service`
- [X] T018 Create `src/utils/output_router.py`: `OutputRouter` class with `post_forecast(division, content)` (writes to `division.forecast_channel_id`) and `post_log(server_id, content)` (writes to `log_channel_id`); both methods catch `discord.HTTPException`, log the failure internally, and surface an alert to the interaction channel rather than crashing

**Checkpoint**: All models importable; `math_utils` functions are unit-testable in isolation; DB migrations run against a fresh SQLite file without error

---

## Phase 3: User Story 1 — Season Setup by a Trusted Admin (Priority: P1) 🎯 MVP

**Goal**: A trusted admin can configure a complete season interactively and receive confirmation that the bot is armed for weather generation.

**Independent Test**: Invoke `/season setup` with a single division and two rounds, run through the interactive prompts, issue `/season review`, then `/season approve`. Verify the season record in the DB has `status = ACTIVE` and that attempting the same command from an unprivileged user returns an ephemeral permission error.

- [X] T019 [US1] Create `src/services/config_service.py`: `get_server_config(server_id) -> ServerConfig | None`; `save_server_config(cfg: ServerConfig)`; `validate_role(guild, role_id)` and `validate_channel(guild, channel_id)` helpers that fetch the Discord object and raise `ValueError` if not found
- [X] T020 [US1] Create `src/services/season_service.py`: `create_season(server_id, start_date) -> Season`; `add_division(season_id, ...) -> Division`; `add_round(division_id, round_number, format, track, scheduled_at) -> Round`; `create_sessions_for_round(round_id, format)` (inserts `Session` rows using `SESSIONS_BY_FORMAT`); `get_active_season(server_id) -> Season | None`; `transition_to_active(season_id)` (sets `status = ACTIVE`); `get_division_rounds(division_id) -> list[Round]`
- [X] T021 [US1] Create `src/cogs/season_cog.py` with `/season setup` slash command: opens interactive step-by-step prompt session (stored in `bot.pending_configs[user_id]`); collects season start date → number of divisions → per-division name/role/channel/race day/time → number of rounds per division → per-round format + track (validated against `TRACKS`) + scheduled date; stores in-progress config in memory; all responses ephemeral
- [X] T022 [US1] Add `/season review` command to `src/cogs/season_cog.py`: retrieves pending config for invoking user; formats and posts a complete summary embed; presents Approve / Amend buttons via `discord.ui.View`; Amend button re-opens targeted step of the prompt flow
- [X] T023 [US1] Add `/season approve` command (and Approve button handler) to `src/cogs/season_cog.py`: calls `season_service.create_season`, `add_division`, `add_round`, `create_sessions_for_round` for all configured data; calls `season_service.transition_to_active`; calls `scheduler_service.schedule_all_rounds`; clears `pending_configs[user_id]`; posts ephemeral confirmation
- [X] T024 [P] [US1] Add `/season status` command to `src/cogs/season_cog.py`: read-only summary of active season (divisions, next round per division, current phase status); ephemeral response
- [X] T025 [US1] Apply `channel_guard` decorator to all commands in `src/cogs/season_cog.py`; add trusted-admin role check (separate from interaction role) to `/season setup`, `/season approve`, and `/season review`
- [X] T026 [US1] Register `SeasonCog` in `src/bot.py` via `await bot.add_cog(SeasonCog(bot, config_service, season_service))`

**Checkpoint**: US1 fully functional. A test Discord server can run the full setup flow end-to-end. Unprivileged and out-of-channel attempts are correctly rejected.

---

## Phase 4: User Story 2 — Automated Three-Phase Weather Generation (Priority: P2)

**Goal**: All three phases fire autonomously at their correct horizons for every non-Mystery round, posting to the right channels, with full computation logs. Survives bot restart.

**Independent Test**: Configure a single-division single-round season, monkey-patch `datetime.utcnow()` to simulate each horizon, and assert (1) the correct message appears in the mock forecast channel, (2) the correct log appears in the mock log channel, (3) a `PhaseResult` row exists in the DB with `status = ACTIVE`.

- [X] T027 [US2] Create `src/services/scheduler_service.py`: instantiate `AsyncIOScheduler` with `SQLAlchemyJobStore` pointing at the same SQLite file; `schedule_round(round: Round)` creates three `DateTrigger` jobs with ids `phase1_r{round.id}`, `phase2_r{round.id}`, `phase3_r{round.id}` at `scheduled_at - 5d`, `- 2d`, `- 2h` respectively; skips all three for `MYSTERY` rounds; `replace_existing=True` on all `add_job` calls; `cancel_round(round_id)` removes all three jobs; `schedule_all_rounds(division_id)` iterates all rounds and calls `schedule_round`
- [X] T028 [P] [US2] Create `src/services/phase1_service.py`: `run_phase1(round_id)` — load `Round` + `Division`; draw `rand1`, `rand2` ∈ [1, 98]; compute `Rpc` via `math_utils.compute_rpc`; persist `PhaseResult` with full payload; update `round.phase1_done = True`; call `output_router.post_forecast` with `message_builder.phase1_message`; call `output_router.post_log` with computation record
- [X] T029 [P] [US2] Create `src/services/phase2_service.py`: `run_phase2(round_id)` — load active Phase 1 `PhaseResult` to get `Rpc`; compute `Ir`, `Im`, `Is` via `math_utils`; pad with mixed slots if sum ≠ 1000; build 1000-entry list; for each session draw 1 entry; persist draw result onto `Session.phase2_slot_type`; persist `PhaseResult`; update `round.phase2_done`; post forecast + log via `output_router`
- [X] T030 [P] [US2] Create `src/services/phase3_service.py`: `run_phase3(round_id)` — for each session load `phase2_slot_type` and active Phase 1 `Rpc`; draw `Nslots` (random in [min, max_slots], min=2 if mixed); build per-session weighted map using `math_utils` weight functions with `clamp_weight`; handle all-zero fallback; draw `Nslots` times; persist slot list onto `Session.phase3_slots`; clear map; persist `PhaseResult`; update `round.phase3_done`; post natural-language message + log via `output_router`
- [X] T031 [US2] Wire scheduler job callbacks in `src/services/scheduler_service.py` to call the correct service: `phase1_r*` → `phase1_service.run_phase1`, etc.; inject `bot` reference via `misfire_grace_time=300` (5-min grace) so late fires still execute
- [X] T032 [US2] Implement restart-recovery in `src/bot.py` `on_ready`: after starting scheduler, query DB for all rounds where a phase is not done but its horizon has already passed; re-fire the missed phases in order (Phase 1 before Phase 2, Phase 2 before Phase 3) before the scheduler's own pending jobs run

**Checkpoint**: US2 fully functional. All three phases fire at correct times, log channel receives computation records, and a simulated restart correctly re-executes any missed phases.

---

## Phase 5: User Story 3 — Post-Approval Configuration Amendment (Priority: P3)

**Goal**: A trusted admin can amend any round's track, date/time, or format at any point during an active season; the bot atomically invalidates prior weather, notifies drivers, and re-executes phases as needed.

**Independent Test**: Season approved, Phase 1 completed. Amend the round's track. Assert: `PhaseResult` for Phase 1 has `status = INVALIDATED`; invalidation message posted to forecast channel; a new Phase 1 `PhaseResult` with `status = ACTIVE` exists using the new track's `Btrack`; `round.track_name` updated in DB.

- [X] T033 [US3] Create `src/services/amendment_service.py`: `amend_round(round_id, actor, field, new_value)` — inside a single DB transaction: load `Round`; record `AuditEntry`; update the round field; for each completed phase mark its `PhaseResult` row `INVALIDATED`; clear `Session.phase2_slot_type` and `Session.phase3_slots` for affected sessions; reset `round.phase1_done / phase2_done / phase3_done` flags; cancel and re-schedule scheduler jobs via `scheduler_service`; if any phase was previously done post `output_router.post_forecast(message_builder.invalidation_message(track))`; then immediately re-run any phase whose new horizon has already passed
- [X] T034 [P] [US3] Create `src/cogs/amendment_cog.py`: `/round amend` slash command accepting `round_number`, `division_name`, and optional `track`, `date`, `time`, `format` parameters; validates all provided values; shows ephemeral confirm/cancel `discord.ui.View` before executing; on confirm calls `amendment_service.amend_round` for each provided field
- [X] T035 [US3] Apply `channel_guard` + trusted-admin check to all commands in `src/cogs/amendment_cog.py`; unprivileged users receive ephemeral permission error; no state changes before the check passes
- [X] T036 [US3] Ensure `amendment_service.amend_round` writes an `AuditEntry` for every field changed and posts the entry to the log channel via `output_router.post_log`
- [X] T037 [US3] Register `AmendmentCog` in `src/bot.py` via `await bot.add_cog(AmendmentCog(bot, config_service, amendment_service))`

**Checkpoint**: US3 fully functional. Amendment flow handles pre-phase, mid-phase, and post-all-phases cases correctly. Audit trail visible in log channel.

---

## Phase 6: User Story 4 — Bot Initialisation (Priority: P4)

**Goal**: A server administrator can run a one-time `/bot init` command to register the interaction role, the interaction channel, and the calculation log channel. All other commands remain gated behind the result of this configuration.

**Independent Test**: Fresh server, no prior config. Issue `/bot init` as a non-guild-admin → expect ephemeral error. Issue as guild admin with valid role + two channels → expect confirmation; verify `server_configs` row in DB. Issue any season command out-of-channel → confirm silent ignore. Issue in-channel without role → confirm ephemeral permission error.

- [X] T038 [US4] Create `src/cogs/init_cog.py`: `/bot init` slash command requiring Discord `MANAGE_GUILD` permission (not the interaction role — chicken-and-egg); accepts `interaction_role: discord.Role`, `interaction_channel: discord.TextChannel`, `log_channel: discord.TextChannel`; validates all three objects; calls `config_service.save_server_config`; posts ephemeral confirmation
- [X] T039 [US4] Add re-init guard to `/bot init` in `src/cogs/init_cog.py`: if `config_service.get_server_config(server_id)` already returns a config, warn the admin and require an explicit `force: bool = False` parameter set to `True` to overwrite
- [X] T040 [US4] Register `InitCog` in `src/bot.py`; confirm `channel_guard` is NOT applied to `/bot init` (it must work before a config exists); apply `channel_guard` to all other cogs only after config is available

**Checkpoint**: US4 fully functional. Full end-to-end flow from zero config → init → season setup → weather generation is exercisable.

---

## Final Phase: Polish & Cross-Cutting Concerns

- [X] T041 [P] Add `Rpc` out-of-range detection in `src/services/phase1_service.py`: after computing `Rpc`, if `Rpc < 0.0` or `Rpc > 1.0` before clamping, call `output_router.post_log` with an anomaly warning; then apply clamp before downstream use
- [X] T042 [P] Add Discord channel unavailability handling in `src/utils/output_router.py`: catch `discord.HTTPException` / `discord.Forbidden` on every channel write; on failure attempt to post an error summary to the log channel; if the log channel itself fails, post to the interaction channel; never raise unhandled into a scheduler job
- [X] T043 Create `README.md` at repo root: prerequisites, `pip install -r requirements.txt`, `.env` setup, `python src/bot.py` run command, and brief slash-command reference (`/bot init`, `/season setup`, `/season review`, `/season approve`, `/season status`, `/round amend`)

---

## Dependencies

```
Phase 2 (Foundational) must complete before any story phase begins.

US1 (P1)  ←  Phase 2 complete
US2 (P2)  ←  US1 complete (needs season_service, scheduler_service armed at season_approve)
US3 (P3)  ←  US1 + US2 complete (amendment_service calls scheduler_service + phase services)
US4 (P4)  ←  Phase 2 complete (init_cog calls config_service, which is created in US1 T019)
              Note: T019 (config_service) can be extracted earlier if US4 is implemented
              out of priority order — the service exists; only the cog is US4-specific.
Polish    ←  all stories complete
```

## Parallel Execution Examples

**Phase 2** — all model files (T007–T014) are independent and can be written simultaneously.

**Phase 4 (US2)** — T028 (phase1_service), T029 (phase2_service), T030 (phase3_service) touch separate files and can proceed in parallel once T027 (scheduler_service) is complete.

**Phase 5 (US3)** — T033 (amendment_service) and T034 (amendment_cog) can proceed in parallel; cog simply awaits the service's method signature.

## Implementation Strategy

- **MVP** = Phase 1 + Phase 2 + Phase 3 (US1 season setup) alone yields a bot that can be configured and run. It won't fire phases yet but proves the entire interactive setup flow.
- **First usable release** = MVP + Phase 4 (US2): bot configures _and_ delivers weather autonomously.
- **Full feature** = all phases complete.

**Total tasks**: 43  
**Parallelisable**: T003, T004, T007–T014, T016, T024, T028–T030, T034, T036, T041, T042 (16 tasks)
