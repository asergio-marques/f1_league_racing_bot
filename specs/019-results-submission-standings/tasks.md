# Tasks: Results & Standings — Points Config, Submission, and Standings (019)

**Input**: Design documents from `specs/019-results-submission-standings/`
**Prerequisites**: plan.md ✅ | spec.md ✅ | research.md ✅ | data-model.md ✅ | quickstart.md ✅ | contracts/ ✅

**Organization**: Tasks grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to ([US1]–[US8])

---

## Phase 1: Setup

**Purpose**: Database migration — hard prerequisite for all model, service, and cog work.

- [X] T001 Create `src/db/migrations/017_results_core.sql` with 13 tables in FK-safe creation order: (1) `points_config_store` (id PK AUTOINCREMENT, server_id INTEGER NOT NULL FK → `server_configs(server_id)` ON DELETE CASCADE, config_name TEXT NOT NULL, UNIQUE(server_id, config_name)); (2) `points_config_entries` (id PK AUTOINCREMENT, config_id INTEGER NOT NULL FK → `points_config_store(id)` ON DELETE CASCADE, session_type TEXT NOT NULL, position INTEGER NOT NULL, points INTEGER NOT NULL DEFAULT 0, UNIQUE(config_id, session_type, position)); (3) `points_config_fl` (id PK AUTOINCREMENT, config_id INTEGER NOT NULL FK → `points_config_store(id)` ON DELETE CASCADE, session_type TEXT NOT NULL, fl_points INTEGER NOT NULL DEFAULT 0, fl_position_limit INTEGER NULL, UNIQUE(config_id, session_type)); (4) `season_points_entries` (id PK AUTOINCREMENT, season_id INTEGER NOT NULL FK → `seasons(id)` ON DELETE CASCADE, config_name TEXT NOT NULL, session_type TEXT NOT NULL, position INTEGER NOT NULL, points INTEGER NOT NULL DEFAULT 0, UNIQUE(season_id, config_name, session_type, position)); (5) `season_points_fl` (id PK AUTOINCREMENT, season_id INTEGER NOT NULL FK → `seasons(id)` ON DELETE CASCADE, config_name TEXT NOT NULL, session_type TEXT NOT NULL, fl_points INTEGER NOT NULL DEFAULT 0, fl_position_limit INTEGER NULL, UNIQUE(season_id, config_name, session_type)); (6) `season_amendment_state` (season_id INTEGER PRIMARY KEY FK → `seasons(id)` ON DELETE CASCADE, amendment_active INTEGER NOT NULL DEFAULT 0, modified_flag INTEGER NOT NULL DEFAULT 0); (7) `season_modification_entries` (id PK AUTOINCREMENT, season_id INTEGER NOT NULL FK → `seasons(id)` ON DELETE CASCADE, config_name TEXT NOT NULL, session_type TEXT NOT NULL, position INTEGER NOT NULL, points INTEGER NOT NULL DEFAULT 0, UNIQUE(season_id, config_name, session_type, position)); (8) `season_modification_fl` (id PK AUTOINCREMENT, season_id INTEGER NOT NULL FK → `seasons(id)` ON DELETE CASCADE, config_name TEXT NOT NULL, session_type TEXT NOT NULL, fl_points INTEGER NOT NULL DEFAULT 0, fl_position_limit INTEGER NULL, UNIQUE(season_id, config_name, session_type)); (9) `session_results` (id PK AUTOINCREMENT, round_id INTEGER NOT NULL FK → `rounds(id)` ON DELETE CASCADE, division_id INTEGER NOT NULL FK → `divisions(id)`, session_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE', config_name TEXT NULL, submitted_by INTEGER NULL, submitted_at TEXT NULL, results_message_id INTEGER NULL, UNIQUE(round_id, session_type)); (10) `driver_session_results` (id PK AUTOINCREMENT, session_result_id INTEGER NOT NULL FK → `session_results(id)` ON DELETE CASCADE, driver_user_id INTEGER NOT NULL, team_role_id INTEGER NOT NULL, finishing_position INTEGER NOT NULL, outcome TEXT NOT NULL DEFAULT 'CLASSIFIED', tyre TEXT NULL, best_lap TEXT NULL, gap TEXT NULL, total_time TEXT NULL, fastest_lap TEXT NULL, time_penalties TEXT NULL, post_steward_total_time TEXT NULL, post_race_time_penalties TEXT NULL, points_awarded INTEGER NOT NULL DEFAULT 0, fastest_lap_bonus INTEGER NOT NULL DEFAULT 0, is_superseded INTEGER NOT NULL DEFAULT 0); (11) `driver_standings_snapshots` (id PK AUTOINCREMENT, round_id INTEGER NOT NULL FK → `rounds(id)` ON DELETE CASCADE, division_id INTEGER NOT NULL FK → `divisions(id)`, driver_user_id INTEGER NOT NULL, standing_position INTEGER NOT NULL, total_points INTEGER NOT NULL DEFAULT 0, finish_counts TEXT NOT NULL DEFAULT '{}', first_finish_rounds TEXT NOT NULL DEFAULT '{}', standings_message_id INTEGER NULL, UNIQUE(round_id, division_id, driver_user_id)); (12) `team_standings_snapshots` (id PK AUTOINCREMENT, round_id INTEGER NOT NULL FK → `rounds(id)` ON DELETE CASCADE, division_id INTEGER NOT NULL FK → `divisions(id)`, team_role_id INTEGER NOT NULL, standing_position INTEGER NOT NULL, total_points INTEGER NOT NULL DEFAULT 0, finish_counts TEXT NOT NULL DEFAULT '{}', first_finish_rounds TEXT NOT NULL DEFAULT '{}', UNIQUE(round_id, division_id, team_role_id)); (13) `round_submission_channels` (id PK AUTOINCREMENT, round_id INTEGER NOT NULL FK → `rounds(id)` ON DELETE CASCADE, channel_id INTEGER NOT NULL, created_at TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0, UNIQUE(round_id))

**Checkpoint**: Migration in place — model and service implementation can begin.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data models that all services and cogs depend on.

**⚠️ CRITICAL**: No service or cog work in Phase 3–10 can begin without these model definitions.

- [X] T002 [P] Create `src/models/points_config.py` — `from __future__ import annotations`; define `SessionType(str, enum.Enum)` with values `SPRINT_QUALIFYING = "SPRINT_QUALIFYING"`, `SPRINT_RACE = "SPRINT_RACE"`, `FEATURE_QUALIFYING = "FEATURE_QUALIFYING"`, `FEATURE_RACE = "FEATURE_RACE"`, add `@property is_race(self) -> bool` (True for SPRINT_RACE/FEATURE_RACE) and `@property is_qualifying(self) -> bool`; `@dataclass PointsConfigStore(id: int, server_id: int, config_name: str)`; `@dataclass PointsConfigEntry(id: int, config_id: int, session_type: SessionType, position: int, points: int)`; `@dataclass PointsConfigFastestLap(id: int, config_id: int, session_type: SessionType, fl_points: int, fl_position_limit: int | None)`

- [X] T003 [P] Create `src/models/session_result.py` — `from __future__ import annotations`; define `OutcomeModifier(str, enum.Enum)` with values `CLASSIFIED = "CLASSIFIED"`, `DNF = "DNF"`, `DNS = "DNS"`, `DSQ = "DSQ"`, add `@property is_points_eligible(self) -> bool` (True for CLASSIFIED only), `@property is_fl_eligible(self) -> bool` (True for CLASSIFIED and DNF); `@dataclass SessionResult(id: int, round_id: int, division_id: int, session_type: SessionType, status: str, config_name: str | None, submitted_by: int | None, submitted_at: str | None, results_message_id: int | None = None)`; `@dataclass DriverSessionResult(id: int, session_result_id: int, driver_user_id: int, team_role_id: int, finishing_position: int, outcome: OutcomeModifier, tyre: str | None, best_lap: str | None, gap: str | None, total_time: str | None, fastest_lap: str | None, time_penalties: str | None, post_steward_total_time: str | None, post_race_time_penalties: str | None, points_awarded: int, fastest_lap_bonus: int, is_superseded: bool)`

- [X] T004 [P] Create `src/models/standings_snapshot.py` — `from __future__ import annotations`; `import json`; `@dataclass DriverStandingsSnapshot(id: int, round_id: int, division_id: int, driver_user_id: int, standing_position: int, total_points: int, finish_counts: dict[str, int], first_finish_rounds: dict[str, int], standings_message_id: int | None = None)` with `@classmethod from_row(cls, row)` that json.loads finish_counts and first_finish_rounds from TEXT; `@dataclass TeamStandingsSnapshot(id: int, round_id: int, division_id: int, team_role_id: int, standing_position: int, total_points: int, finish_counts: dict[str, int], first_finish_rounds: dict[str, int])` same from_row pattern

- [X] T005 [P] Create `src/models/amendment_state.py` — `from __future__ import annotations`; `@dataclass SeasonAmendmentState(season_id: int, amendment_active: bool, modified_flag: bool)`; `@dataclass SeasonModificationEntry(id: int, season_id: int, config_name: str, session_type: SessionType, position: int, points: int)`; `@dataclass SeasonModificationFl(id: int, season_id: int, config_name: str, session_type: SessionType, fl_points: int, fl_position_limit: int | None)`

**Checkpoint**: All data models available — Phase 3 service work can begin in parallel.

---

## Phase 3: User Story 1 — Trusted Admin Manages Points Configurations (Priority: P1) 🎯 MVP

**Goal**: Trusted admins can create named configs, set per-position points and FL bonuses, attach configs to a season in SETUP, and have them snapshotted on season approval. A monotonic-ordering gate blocks approval of invalid configs.

**Independent Test**: Create a config "100%"; set 1st→25pts, 2nd→18pts in FEATURE_RACE; set FL bonus 1pt with position limit 10; attach to a season in SETUP; run `season review → Approve`; confirm the season's points store contains the snapshot. Create a second config with 1st→10, 2nd→18 (non-monotonic); attach; attempt approval; confirm it is blocked with a diagnostic.

- [X] T006 [P] [US1] Create `src/services/points_config_service.py` — `from __future__ import annotations`; async functions: `create_config(db_path, server_id, config_name)`: INSERT INTO points_config_store; raise `ConfigAlreadyExistsError` if UNIQUE constraint violated; `remove_config(db_path, server_id, config_name)`: SELECT id first, raise `ConfigNotFoundError` if absent, then DELETE (CASCADE removes child rows); `set_session_points(db_path, server_id, config_name, session_type: SessionType, position: int, points: int)`: SELECT config id for (server_id, config_name), raise `ConfigNotFoundError` if absent; INSERT OR REPLACE into points_config_entries; `set_fl_bonus(db_path, server_id, config_name, session_type: SessionType, fl_points: int)`: raise `InvalidSessionTypeError` if session_type.is_qualifying; resolve config id; INSERT OR REPLACE into points_config_fl (fl_position_limit unchanged if row already exists — use COALESCE pattern or separate value fetch); `set_fl_position_limit(db_path, server_id, config_name, session_type: SessionType, limit: int)`: same qualifying guard; resolve config id; INSERT OR REPLACE into points_config_fl; `get_config_entries(db_path, server_id, config_name)` → `tuple[list[PointsConfigEntry], list[PointsConfigFastestLap]]`: SELECT from points_config_entries and points_config_fl by `(server_id, config_name)` via points_config_store join; `list_configs(db_path, server_id)` → `list[PointsConfigStore]`: SELECT all from points_config_store WHERE server_id

- [X] T007 [P] [US1] Create `src/services/season_points_service.py` — async functions: `attach_config(db_path, season_id, config_name, season_status)`: raise `SeasonNotInSetupError` if season_status != "SETUP"; INSERT INTO season_points_links; raise `ConfigAlreadyAttachedError` on UNIQUE conflict; `detach_config(db_path, season_id, config_name, season_status)`: same SETUP gate; DELETE from season_points_links WHERE (season_id, config_name); raise `ConfigNotAttachedError` if rowcount==0; `get_attached_config_names(db_path, season_id)` → `list[str]`: SELECT config_name from season_points_links WHERE season_id; `snapshot_configs_to_season(db_path, season_id, server_id)`: for each config_name from `get_attached_config_names`, call `points_config_service.get_config_entries`; INSERT into season_points_entries for every PointsConfigEntry row; INSERT into season_points_fl for every PointsConfigFastestLap row; operate inside a single BEGIN/COMMIT transaction; `validate_monotonic_ordering(db_path, season_id)` → `list[str]`: SELECT config_name, session_type, position, points FROM season_points_entries WHERE season_id ORDER BY config_name, session_type, position; group and assert points[i] >= points[i+1]; return list of error strings each formatted as `"Config '{name}', {session}: position {n} ({p} pts) < position {m} ({q} pts)"`; `get_season_points_view(db_path, season_id, config_name, session_type_filter: SessionType | None = None)` → `dict[str, list[tuple[int, int]]]`: SELECT from season_points_entries (and season_points_fl for FL row); if session_type_filter given, return only that session; collapse trailing zeros: find last position where points > 0, keep all rows up to and including that position, append sentinel `(last_position+1, 0)` labelled `"{N}th+: 0"` if any trailing zeros exist; also include FL bonus row per session from season_points_fl

- [X] T008 [US1] Extend `SeasonCog._do_approve` in `src/cogs/season_cog.py` — immediately after the existing R&S prerequisites gate (Gate 2, T022 from 018), insert Gate 3 monotonic ordering check: call `await season_points_service.validate_monotonic_ordering(self._db_path, season_id)`; if returned list is non-empty, build bullet-point error string, respond with `❌ Season cannot be approved — points configuration violates monotonic ordering:\n{bullet_list}` using `interaction.followup.send` or `interaction.response.send_message` per existing `is_done()` pattern, then `return`

- [X] T009 [US1] Create `src/cogs/results_cog.py` — `from __future__ import annotations`; `import discord` and app_commands; `ResultsCog(commands.Cog)` class with `__init__(self, bot, db_path)`; private `async _module_gate(interaction) -> bool`: call `is_results_enabled(self._db_path, interaction.guild_id)`; if False respond ephemeral `❌ The Results & Standings module is not enabled on this server.` and return False; else return True; private `async _server_admin_gate(interaction) -> bool`: check `interaction.user.guild_permissions.administrator`; if False respond ephemeral `❌ This command requires server admin permissions.` and return False; else return True; implement `/results config add [name]`: module gate; defer ephemeral; call `points_config_service.create_config`; respond `✅ Config **{name}** created. All positions default to 0 points.`; `/results config remove [name]`: module gate; defer; call `remove_config`; respond `✅ Config **{name}** removed.`; `/results config session [name] [session] [position] [points]`: module gate; defer; call `set_session_points`; respond `✅ Set {session} position {position} → {points} pts in config **{name}**.`; `/results config fl [name] [session] [points]`: module gate; defer; call `set_fl_bonus`; handle `InvalidSessionTypeError` → ephemeral `❌`; respond `✅ Set fastest-lap bonus for {session} → {points} pts in config **{name}**.`; `/results config fl-plimit [name] [session] [limit]`: module gate; defer; call `set_fl_position_limit`; handle `InvalidSessionTypeError`; respond `✅ Set fastest-lap position limit for {session} → top {limit} eligible in config **{name}**.`; all error paths from ConfigNotFoundError respond ephemeral `❌ Config **{name}** not found.`

- [X] T010 [US1] Add `/results config append [name]` and `/results config detach [name]` commands to `src/cogs/results_cog.py` — both: module gate; defer ephemeral; call `get_season_for_server(self._db_path, interaction.guild_id)` → raise/respond `❌ No active or setup season found.` if None; check season.status is "SETUP" (respond `❌ Config attachment is only allowed for seasons in SETUP.` if not); append: call `season_points_service.attach_config`; respond `✅ Config **{name}** attached to the current season.`; detach: call `season_points_service.detach_config`; respond `✅ Config **{name}** detached from the current season.`; handle `ConfigAlreadyAttachedError` and `ConfigNotAttachedError` → ephemeral `ℹ️` messages

**Checkpoint**: All config CRUD and attachment commands operational. Monotonic gate blocks non-decreasing configs at season approval.

---

## Phase 4: User Story 2 — Round Result Submission Wizard (Priority: P1)

**Goal**: At each round's scheduled start time, a transient submission channel is created and trusted admins submit results session by session. The wizard validates each line, prompts for config selection, logs all inputs, and closes the channel when complete.

**Independent Test**: Schedule a round with a Feature-format division. At round start time, confirm a channel named `results-submission-{div-slug}-r{N}` is created in the results channel's category. Submit valid FEATURE_QUALIFYING results (multi-line); confirm accepted. Submit invalid results (wrong position order); confirm rejected with specific error. Submit CANCELLED for FEATURE_RACE; confirm channel closes and no driver rows are written for that session. Confirm the round cancel command is blocked while the submission channel is open.

- [X] T011 [US2] Extend `src/services/scheduler_service.py` — in `schedule_round(round, db_path, bot, scheduler)`: after registering `cleanup_r{round.id}`, append `scheduler.add_job(_result_submission_job, DateTrigger(run_date=round.scheduled_at), id=f"results_r{round.id}", replace_existing=True, args=[round.id, db_path, bot])`; wrap in a try/except to protect against the job function not yet existing at import time (forward reference); in `cancel_round(round_id, scheduler)`: add `scheduler.remove_job(f"results_r{round_id}")` with the same `silent-fail` pattern used for existing job IDs (catch `JobLookupError`); define module-level constant `RESULT_JOB_PREFIX = "results_r"`

- [X] T012 [US2] Create `src/services/result_submission_service.py` — `from __future__ import annotations`; constant `SESSION_ORDER_NORMAL = [SessionType.FEATURE_QUALIFYING, SessionType.FEATURE_RACE]`; constant `SESSION_ORDER_SPRINT = [SessionType.SPRINT_QUALIFYING, SessionType.SPRINT_RACE, SessionType.FEATURE_QUALIFYING, SessionType.FEATURE_RACE]`; `get_sessions_for_format(round_format: RoundFormat) -> list[SessionType]`: return SESSION_ORDER_SPRINT if round_format is SPRINT, else SESSION_ORDER_NORMAL (both NORMAL and ENDURANCE use Feature sessions only per research.md §8); `async create_submission_channel(guild, results_channel, division_name_slug: str, round_number: int, db_path) -> discord.TextChannel`: call `await guild.create_text_channel(name=f"results-submission-{division_name_slug}-r{round_number}", category=results_channel.category)`, INSERT INTO round_submission_channels(round_id, channel_id, created_at, closed=0); return channel; `async close_submission_channel(channel_id: int, round_id: int, db_path)`: UPDATE round_submission_channels SET closed=1 WHERE round_id; `await guild.get_channel(channel_id).delete(reason="Results submission complete")`; `async is_submission_open(db_path, round_id: int) -> bool`: SELECT closed FROM round_submission_channels WHERE round_id; return True if row exists and closed==0; `async save_session_result(db_path, round_id, division_id, session_type: SessionType, status: str, config_name: str | None, submitted_by: int | None, submitted_at: str, driver_rows: list[dict]) -> int`: INSERT INTO session_results; get lastrowid; bulk INSERT INTO driver_session_results; return session_result_id

- [X] T013 [US2] Add validation helpers to `src/services/result_submission_service.py` — `@dataclass ParsedQualifyingRow(position, driver_user_id, team_role_id, tyre, best_lap, gap, outcome: OutcomeModifier)`; `@dataclass ParsedRaceRow(position, driver_user_id, team_role_id, total_time, fastest_lap, time_penalties, outcome: OutcomeModifier)`; reuse existing lap-time regexes from the codebase (search for `_LAP_TIME_RE` or equivalent in `src/services/` or `src/utils/`); `_parse_mention(text) -> int | None`: extract snowflake from `<@!?(\d+)>` or `<@&(\d+)>`; define regex constants: `_TIME_RE` (absolute: `\d+:\d{2}\.\d{3}` or `\d{2}\.\d{3}` or `\d+:\d{2}:\d{2}\.\d{3}`), `_DELTA_RE` (`\+\d+:\d{2}\.\d{3}` etc.), `_LAP_GAP_RE` (`\+?\d+ Laps?`); `validate_qualifying_row(line: str) -> ParsedQualifyingRow | ValidationError`: split by comma or whitespace per the 6-field format; validate position is int; driver is mention; team is role mention; best_lap matches `_TIME_RE` or is DNS/DNF/DSQ; gap matches `_DELTA_RE` or is "N/A"; `validate_race_row(line: str, is_first: bool) -> ParsedRaceRow | ValidationError`: validate position int; driver mention; team role mention; total_time matches `_TIME_RE` if is_first else `_TIME_RE | _DELTA_RE | DNS/DNF/DSQ | _LAP_GAP_RE`; time_penalties matches `_TIME_RE` or "N/A"; `validate_submission_block(lines: list[str], session_type: SessionType, division_driver_ids: set[int], team_role_ids: set[int], reserve_team_role_id: int | None, driver_team_map: dict[int, int]) -> list[ParsedRow] | list[str]`: returns list of parsed rows or list of error message strings; check no gaps in positions (pos 1 through N must be contiguous); check each driver_user_id in division_driver_ids; check each team_role_id in team_role_ids; check driver's team matches driver_team_map[driver_user_id] OR team is reserve_team_role_id

- [X] T014 [US2] Implement `async _result_submission_job(round_id: int, db_path: str, bot)` as a module-level async function in `src/services/result_submission_service.py` — (1) check `is_results_enabled(db_path, server_id)` via DB read; return silently if disabled; (2) load round from DB; load division and its results_channel_id; get guild via `bot.get_guild(server_id)`; (3) call `create_submission_channel`; (4) send opening message to channel: `✅ Results submission open for **Round {N}** ({division_name}). Sessions: {session_list}.` + mention `@trusted-admin-role-id` if configured; (5) for each session_type in `get_sessions_for_format(round.format)`: send `📋 Submit **{session_type_label}** results (one driver per line), or type \`CANCELLED\`.`; loop: `msg = await bot.wait_for("message", check=lambda m: m.channel.id == channel.id and not m.author.bot, timeout=None)`; if msg.content.strip().upper() == "CANCELLED": call `save_session_result(status="CANCELLED", driver_rows=[])`; send `✅ {session_type_label} marked as CANCELLED.`; break inner loop; else: call `validate_submission_block`; if errors: log raw input to audit log (`RESULT_SUBMISSION_ATTEMPT`); send `❌ Validation failed:\n{error_list}\nPlease resubmit.`; loop again; on success: log raw input to audit log (`RESULT_SUBMISSION_ACCEPTED`); (6) after valid block: check attached config names for this season — if exactly one, auto-select it; else post `discord.ui.View(timeout=None)` with one button per config_name; await button press via view; (7) call `save_session_result(status="ACTIVE", config_name=selected, driver_rows=[...])` with computed outcome modifiers (DNS/DNF/DSQ from best_lap/total_time field text); (8) after all sessions complete, call `standings_service.compute_and_persist_round(round_id, division_id, db_path)` then `results_post_service.post_round_results(round_id, division_id, guild, db_path)` then `results_post_service.post_standings(division_id, round_id, guild, db_path)`; (9) call `close_submission_channel(channel.id, round_id, db_path)`

- [X] T015 [US2] Guard `/round cancel` (or equivalent cancel command in `src/cogs/round_cog.py`) — before processing the cancel: call `await result_submission_service.is_submission_open(self._db_path, round_id)`; if True, respond ephemeral `❌ Cannot cancel this round — a results submission channel is currently open. Close the submission first.` and return (per FR-020)

**Checkpoint**: APScheduler triggers the submission job at round start time. Full session wizard runs. Results and config choice persisted to DB. Round cancel blocked while channel is live.

---

## Phase 5: User Story 3 — Results & Standings Output (Priority: P1)

**Goal**: After all sessions in a round are submitted, formatted results and standings are posted to the division's configured channels and can be reposted after amendments.

**Independent Test**: Complete a round submission. Confirm formatted tables appear in the results channel (qualifying table: Position/Driver/Team/Tyre/Best Lap/Gap/Points Gained; race table: Position/Driver/Team/Total Time/Fastest Lap/Time Penalties/Points Gained). Confirm standings appear in the standings channel. Amend any driver result and confirm both channels are updated in-place (edit, not new message).

- [ ] T016 [P] [US3] Create `src/utils/results_formatter.py` — `from __future__ import annotations`; `_collapse_trailing_zeros(rows: list[tuple[int, int]]) -> list[tuple[str, int]]`: find last index where points > 0; return all rows up to and including that index as `("{pos}", pts)` tuples; if rows exist beyond that index, append `("{n}th+", 0)` sentinel using the next position; `format_session_label(session_type: SessionType) -> str`: returns human-readable name ("Feature Qualifying", "Feature Race", etc.); `format_qualifying_table(driver_rows: list[DriverSessionResult], points_by_driver: dict[int, int], member_display: dict[int, str], team_display: dict[int, str]) -> str`: build fixed-width or code-block table with header row `Pos | Driver | Team | Tyre | Best Lap | Gap | Points`; `format_race_table(driver_rows: list[DriverSessionResult], points_by_driver: dict[int, int], member_display: dict[int, str], team_display: dict[int, str]) -> str`: header `Pos | Driver | Team | Total Time | Fastest Lap | Time Penalties | Points`; `format_driver_standings(snapshots: list[DriverStandingsSnapshot], member_display: dict[int, str], reserve_user_ids: set[int], show_reserves: bool) -> str`: ranked list; omit drivers where driver_user_id in reserve_user_ids if not show_reserves; format `{pos}. {name} — {total_points} pts`; `format_team_standings(snapshots: list[TeamStandingsSnapshot], team_display: dict[int, str]) -> str`: same pattern for teams; `format_config_view(config_name: str, entries_by_session: dict[str, list[tuple[int, int]]], fl_by_session: dict[str, tuple[int, int | None]]) -> str`: per session, print collapsed points table + FL bonus row; collapse trailing zeros using `_collapse_trailing_zeros`

- [X] T017 [US3] Create `src/services/standings_service.py` — `from __future__ import annotations`; `compute_points_for_session(driver_rows: list[DriverSessionResult], config_entries: list[PointsConfigEntry], fl_config: PointsConfigFastestLap | None) -> list[DriverSessionResult]`: for each row: if outcome is DNS or DSQ → points_awarded=0, fastest_lap_bonus=0; if outcome is DNF → points_awarded=0, FL eligibility check: if fl_config is not None, check finishing_position <= fl_config.fl_position_limit (or no limit), test is_fl (based on fastest_lap field being non-null/non-N/A or a separate boolean passed in); if eligble → fastest_lap_bonus = fl_config.fl_points; if outcome is CLASSIFIED → lookup position in config_entries for points; FL bonus same eligibility rules; `detect_fastest_lap(driver_rows: list[DriverSessionResult], session_type: SessionType) -> int | None`: for race sessions, compare fastest_lap field values (parse to seconds for comparison); return driver_user_id of fastest or None if no lap posted; `compute_driver_standings(db_path, division_id, up_to_round_id) -> list[DriverStandingsSnapshot]`: aggregate total_points from all non-superseded driver_session_results for this division up to and including the given round; sort by (1) total_points DESC, (2) Feature Race win count DESC, (3) Feature Race 2nd-place count DESC, ... iterating through all positions per FR-028; if still tied after all finish-count tiebreaks, driver who first achieved the highest diverging position wins (using first_finish_rounds map); return snapshots with standing_position assigned starting at 1; `compute_team_standings(db_path, division_id, up_to_round_id) -> list[TeamStandingsSnapshot]`: aggregate total Feature Race points per team_role_id (from driver_session_results where session_type = FEATURE_RACE or SPRINT_RACE per constitution XII) across all non-superseded rows; build finish_counts per team from Feature Race finish positions only; apply same countback hierarchy per FR-029; `async persist_snapshots(db_path, driver_snaps: list[DriverStandingsSnapshot], team_snaps: list[TeamStandingsSnapshot])`: INSERT OR REPLACE (with full row) into driver_standings_snapshots and team_standings_snapshots; `async compute_and_persist_round(db_path, round_id, division_id)`: load all non-superseded driver_session_results for this round and all prior rounds in division; call compute_driver_standings and compute_team_standings; call persist_snapshots; `async cascade_recompute_from_round(db_path, division_id, from_round_id)`: SELECT all rounds for division WHERE round_number >= (SELECT round_number FROM rounds WHERE id = from_round_id) ORDER BY round_number ASC; for each round in order: call compute_and_persist_round

- [X] T018 [US3] Create `src/services/results_post_service.py` — `from __future__ import annotations`; `async post_session_results(db_path, session_result: SessionResult, driver_rows: list[DriverSessionResult], points_map: dict[int, int], results_channel, guild) -> int`: format via `results_formatter.format_qualifying_table` or `format_race_table`; call `await results_channel.send(formatted_text)`; UPDATE session_results SET results_message_id = message.id WHERE id = session_result.id; return message.id; `async post_standings(db_path, division_id, round_id, standings_channel, driver_snapshots: list[DriverStandingsSnapshot], team_snapshots: list[TeamStandingsSnapshot], guild, show_reserves: bool)`: format driver + team tables; check if standings_channel has existing message to edit (store last standings_message_id per division in driver_standings_snapshots row for the top-ranked driver, or use a small lookup); `await message.edit` if prior message id exists and fetch succeeds, else `await standings_channel.send`; `async post_round_results(db_path, round_id, division_id, results_channel, guild)`: load all non-cancelled session_results for round; for each in session order: load driver_session_results; call compute_points_for_session; call post_session_results; `async repost_round_results(db_path, round_id, division_id, guild)`: load division; get results_channel and standings_channel from division config; call post_round_results; recompute standings via standings_service; call post_standings

**Checkpoint**: Results and standings appear in correct division channels after round completion. Edit-in-place works for subsequent amendments.

---

## Phase 6: User Story 4 — Config View Command (Priority: P1)

**Goal**: Trusted admins can view the points configuration currently applied to the active or SETUP season, with optional session filter and trailing-zero position collapsing.

**Independent Test**: Attach config "100%" with 1st→25, 2nd→18, 3rd+→0. Run `/results config view name:100%`; confirm display shows 1st:25, 2nd:18, 3rd+:0 (not individual zero rows). Run with session filter `Feature Race`; confirm only Feature Race is shown. Attempt with no active/SETUP season; confirm error.

- [X] T019 [US4] Add `/results config view [name] (session)` command to `src/cogs/results_cog.py` — module gate; defer ephemeral; call `get_season_for_server(self._db_path, interaction.guild_id)`; if None respond `❌ No active or setup season found.` and return; if season.status == "ACTIVE": call `season_points_service.get_season_points_view(self._db_path, season.id, name, session_type_filter)`; if season.status == "SETUP": first try season store (for configs already snapshotted, which don't yet exist at SETUP), fall back to server-level `points_config_service.get_config_entries(self._db_path, guild_id, name)` and apply same collapse logic; call `results_formatter.format_config_view(name, entries_by_session, fl_by_session)` with collapsed rows; respond ephemeral with formatted string; handle config-not-found → `❌ Config **{name}** not found in the current season.`

**Checkpoint**: `/results config view` shows correct collapsed tables for active and SETUP seasons.

---

## Phase 7: User Story 5 — Post-Race Penalties and Disqualifications (Priority: P2)

**Goal**: Trusted admins invoke a guided penalty wizard for a completed round, stage time penalties and DSQs per session, review the staged list, approve, and the bot recomputes and reposts results and standings from that round onwards.

**Independent Test**: Submit a round with valid results where the 1st-place driver has a faster lap than 2nd. Invoke `/round results penalize`; select Feature Race; enter driver ID + "+5s"; enter a second driver ID + "DSQ"; proceed to Review; approveive. Confirm: 1st-place driver's position recalculated with 5s added; DSQ driver moved to last position with 0 points; standings channel updated; audit log entry recorded.

- [X] T020 [US5] Create `src/services/penalty_service.py` — `from __future__ import annotations`; `@dataclass StagedPenalty(driver_user_id: int, session_type: SessionType, penalty_type: Literal["TIME", "DSQ"], penalty_seconds: int | None)`; `def validate_penalty_input(session_type: SessionType, penalty_value: str) -> StagedPenalty | str`: if session_type.is_qualifying and penalty_value.upper() != "DSQ", return error string `"Only DSQ is accepted for qualifying sessions."`; if penalty_value.upper() == "DSQ", return `StagedPenalty(..., penalty_type="DSQ", penalty_seconds=None)`; parse integer seconds (e.g. `"5"` → `5`, reject negatives); return `StagedPenalty(..., penalty_type="TIME", penalty_seconds=seconds)`; `async apply_penalties(db_path, round_id, division_id, staged: list[StagedPenalty], applied_by: int)`: for each DSQ: UPDATE driver_session_results SET outcome='DSQ', points_awarded=0, fastest_lap_bonus=0 WHERE round.session AND driver_user_id; for each TIME: fetch driver row; add penalty_seconds to total_time (parse HH:MM:SS.mmm → seconds, add, reformat); UPDATE total_time and post_steward_total_time; after all mutations, re-sort positions within each affected session_result: ORDER BY total_time ascending (treating DSQ as infinite time); update finishing_position and gap values across the session; for qualifying DSQ: move to last position, set gap accordingly; call `standings_service.cascade_recompute_from_round(db_path, division_id, round_id)`; INSERT audit log entry: `log_service.post_log(PENALTY_APPLIED, actor=applied_by, season_id, division_id, round_id, details=json.dumps(staged))`; call `results_post_service.repost_round_results(db_path, round_id, division_id, guild)`

- [X] T021 [US5] Implement `/round results penalize [division] [round]` in `src/cogs/results_cog.py` (replacing the stub from the cog skeleton) — module gate + trusted admin gate; defer; resolve division by name (call `get_divisions(self._db_path, season.id)`, find match case-insensitive); resolve round by round_number within division's season; verify round has at least one ACTIVE session_result (else `❌ No results found for this round.`); build `discord.ui.View(timeout=None)` with one button per distinct session_type present in session_results + Cancel button; send `🚦 Select session to apply penalty to:` with view; await button press; on Cancel: respond `ℹ️ Penalty wizard cancelled.`; on session selected: send `👤 Enter driver ID (Discord mention) and penalty on one line, e.g. \`@Driver +5\` or \`@Driver DSQ\`. Type \`done\` when finished or \`review\` to proceed.`; loop: await message (wait_for, timeout=None, same channel, non-bot); if "done" or "review": break; else: parse `@mention penalty_value`; call `penalty_service.validate_penalty_input`; if error respond and loop; else stage and confirm `✅ Staged: {driver} {penalty_type}`; if same driver already has a TIME penalty and a new DSQ is staged, replace TIME with DSQ (DSQ supersedes); post `📋 Review staged penalties:\n{list}` with Approve / Make Changes / Cancel buttons; on Make Changes: loop back to session selection with staged list preserved; on Cancel: discard; on Approve: call `await penalty_service.apply_penalties(self._db_path, round_id, division_id, staged, interaction.user.id)`; respond `✅ Penalties applied and standings updated.`

**Checkpoint**: Penalty wizard collects TIME and DSQ penalties, reviews, approves, updates results/standings, and logs.

---

## Phase 8: User Story 6 — Full Session Amendment (Priority: P2)

**Goal**: Trusted admins re-submit an entire session's results; the previous data is superseded and standings are cascaded from that round forwards.

**Independent Test**: Submit a round. Invoke `/round results amend`; select Feature Race; submit corrected results in the same format. Confirm old DriverSessionResult rows have `is_superseded=1`; confirm new rows exist; confirm standings channel updated from that round through any subsequent rounds.

- [X] T022 [US6] Add `async amend_session_result(db_path, round_id, division_id, session_type: SessionType, new_driver_rows: list[dict], config_name: str, amended_by: int)` to `src/services/result_submission_service.py` — UPDATE driver_session_results SET is_superseded=1 WHERE session_result_id = (SELECT id FROM session_results WHERE round_id AND session_type); UPDATE session_results SET submitted_by=amended_by, submitted_at=now(), config_name=config_name WHERE round_id AND session_type; INSERT new driver_session_results rows (is_superseded=0) via save_session_result helper; INSERT audit log: `RESULT_AMENDED`, actor=amended_by, season_id, division_id, round_id, session_type, details; call `standings_service.cascade_recompute_from_round(db_path, division_id, round_id)`; call `results_post_service.repost_round_results(db_path, round_id, division_id, guild)`

- [X] T023 [US6] Implement `/round results amend [division] [round] (session)` in `src/cogs/results_cog.py` — module gate + trusted admin gate; defer; resolve division and round; if `session` parameter not provided: build session button view (one per ACTIVE session_result); await selection via button press; once session_type determined: send `📋 Submit corrected results for {session_type_label}:`; loop await raw message; call `validate_submission_block`; if invalid: send error, loop again; if valid: determine config_name (auto-select if one config in season, else post selection View); call `result_submission_service.amend_session_result`; respond `✅ Session amended and standings updated.`

**Checkpoint**: Full session re-entry replaces old driver rows and cascades standings recomputation across all subsequent rounds.

---

## Phase 9: User Story 7 — Mid-Season Points Amendment (Priority: P2)

**Goal**: Server admins enable amendment mode (copying the season store to a modification store), trusted admins make changes, and a server admin reviews and approves atomically. Toggle-off is blocked while changes are uncommitted.

**Independent Test**: Enable amendment mode; confirm modification store created. Modify Feature Race 1st-place points from 25 to 30; confirm modified_flag=1. Attempt to toggle off; confirm blocked. Invoke revert; confirm modification store reset and modified_flag=0. Re-modify. Invoke review → Approve. Confirm season_points_entries updated; all divisions' results reposted; modification store purged; amendment_active=0.

- [X] T024 [P] [US7] Create `src/services/amendment_service.py` — async functions: `get_amendment_state(db_path, season_id) -> SeasonAmendmentState | None`: SELECT from season_amendment_state; `enable_amendment_mode(db_path, season_id, server_id)`: INSERT OR REPLACE season_amendment_state(season_id, amendment_active=1, modified_flag=0); DELETE FROM season_modification_entries WHERE season_id; DELETE FROM season_modification_fl WHERE season_id; INSERT INTO season_modification_entries SELECT all from season_points_entries WHERE season_id; INSERT INTO season_modification_fl SELECT all from season_points_fl WHERE season_id; — all in one transaction; `disable_amendment_mode(db_path, season_id)`: SELECT modified_flag; raise `AmendmentModifiedError` if modified_flag=1; DELETE season_modification_entries WHERE season_id; DELETE season_modification_fl WHERE season_id; UPDATE season_amendment_state SET amendment_active=0 WHERE season_id; `revert_modification_store(db_path, season_id)`: DELETE season_modification_entries WHERE season_id; INSERT INTO season_modification_entries SELECT * FROM season_points_entries WHERE season_id; same for fl; UPDATE season_amendment_state SET modified_flag=0 WHERE season_id; `modify_session_points(db_path, season_id, config_name, session_type, position, points)`: verify amendment_active=1 (raise `AmendmentNotActiveError` if not); INSERT OR REPLACE into season_modification_entries; UPDATE season_amendment_state SET modified_flag=1; `modify_fl_bonus(db_path, season_id, config_name, session_type, fl_points)`: same gate; INSERT OR REPLACE season_modification_fl; modified_flag=1; `modify_fl_position_limit(db_path, season_id, config_name, session_type, limit)`: same gate; UPDATE season_modification_fl SET fl_position_limit = limit; modified_flag=1; `get_modification_store_diff(db_path, season_id) -> str`: SELECT from both season_points_entries and season_modification_entries; build side-by-side diff string (show changed rows as `{config}/{session}/{pos}: {old} → {new}`); `async approve_amendment(db_path, season_id, approved_by, guild)`: in one transaction: DELETE season_points_entries WHERE season_id; INSERT INTO season_points_entries SELECT * FROM season_modification_entries WHERE season_id; DELETE season_points_fl WHERE season_id; INSERT INTO season_points_fl SELECT * FROM season_modification_fl WHERE season_id; DELETE season_modification_entries WHERE season_id; DELETE season_modification_fl WHERE season_id; UPDATE season_amendment_state SET amendment_active=0, modified_flag=0; INSERT audit log (AMENDMENT_APPROVED, approved_by, season_id); after transaction: load all divisions for season; for each division call `standings_service.cascade_recompute_from_round(db_path, division_id, first_round_id_for_division)` and `results_post_service.repost_round_results` for all rounds in division

- [X] T025 [US7] Add `/results amend toggle`, `/results amend revert`, `/results amend session`, `/results amend fl`, `/results amend fl-plimit`, `/results amend review` commands to `src/cogs/results_cog.py` — `toggle` [SA]: module gate + server-admin gate; resolve active season; call `get_amendment_state`; if currently off: call `enable_amendment_mode`, respond `✅ Amendment mode enabled. Modification store initialised.`; if currently on: call `disable_amendment_mode`; handle `AmendmentModifiedError` → `❌ Cannot disable amendment mode — uncommitted changes exist. Use \`/results amend revert\` to discard or \`/results amend review\` to apply.`; else respond `✅ Amendment mode disabled.`; `revert` [T2]: module gate; resolve season; call `revert_modification_store`; respond `✅ Modification store reverted to current season points.`; `session/fl/fl-plimit` [T2]: module gate; verify amendment active; call respective amendment_service modify method; respond `✅ Updated in modification store.`; `review` [SA]: module gate + server-admin gate; call `get_modification_store_diff`; build response with diff text + Approve / Reject buttons (View timeout=None); on Approve: call `await amendment_service.approve_amendment`; respond `✅ Amendment approved. All standings recomputed and reposted.`; on Reject: respond `ℹ️ Amendment rejected. Modification store and amendment mode remain active.`

**Checkpoint**: Full amendment mode lifecycle works: enable → modify → review → approve cascades all standings. Toggle-off blocked while modified.

---

## Phase 10: User Story 8 — Reserve Driver Visibility Toggle (Priority: P3)

**Goal**: A trusted admin can hide reserve drivers from publicly posted standings without affecting their point accrual or internal snapshots.

**Independent Test**: Toggle reserves visibility off for a division. Submit a round where a reserve driver scores points. Confirm the standings post excludes that driver but internal DriverStandingsSnapshot still contains the row. Toggle on; repost standings; confirm reserve driver appears.

- [X] T026 [US8] Add `/results reserves toggle [division]` command to `src/cogs/results_cog.py` — module gate + trusted admin gate; defer; resolve division from name; toggle `reserves_in_standings` field in `division_results_config`: SELECT current value; UPDATE to opposite; respond `✅ Reserve visibility for **{division}** set to **{"visible" if new_value else "hidden"}**.`; `results_post_service.post_standings` already receives `show_reserves: bool` (from `division_results_config.reserves_in_standings`); no additional changes needed to post logic since T018 already wires this flag

**Checkpoint**: Reserve visibility toggle works. Standings posts respect the flag while internal snapshots are unaffected.

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: Final wiring, registration, and test coverage.

- [X] T027 Register `ResultsCog` in `src/bot.py` setup_hook — add `from src.cogs.results_cog import ResultsCog` import; add `await bot.add_cog(ResultsCog(bot, self.db_path))` in `setup_hook` alongside existing cog registrations

- [X] T028 [P] Write `tests/unit/test_points_config_service.py` — test `create_config` success and duplicate-name conflict; test `remove_config` not-found error; test `set_fl_bonus` raises `InvalidSessionTypeError` for qualifying session types; test `get_config_entries` returns correct PointsConfigEntry and FL rows from temp in-memory DB

- [X] T029 [P] Write `tests/unit/test_season_points_service.py` — test `attach_config` blocked outside SETUP; test `validate_monotonic_ordering` returns empty list for valid config and non-empty list with diagnostic for non-monotonic config; test `get_season_points_view` trailing-zero collapse produces correct `"Nth+: 0"` sentinel

- [X] T030 [P] Write `tests/unit/test_result_submission_service.py` — test `get_sessions_for_format` returns correct session lists for NORMAL, SPRINT, ENDURANCE; test `validate_qualifying_row` rejects wrong position, invalid mention, invalid time format; test `validate_race_row` accepts delta format and DNF/DNS; test `validate_submission_block` catches position gaps and wrong-team driver assignments

- [X] T031 [P] Write `tests/unit/test_standings_service.py` — test `compute_points_for_session` with CLASSIFIED (gets position + FL), DNF (gets FL only), DNS (gets nothing), DSQ (gets nothing); test FL position limit cutoff at configured boundary; test `compute_driver_standings` countback: two drivers equal points, one has more Feature Race wins, confirm correct ordering

- [X] T032 [P] Write `tests/unit/test_results_formatter.py` — test `_collapse_trailing_zeros` with all-zero input, mix of non-zero then zeros, all non-zero; test qualifying and race table headers contain expected column names

- [X] T033 [P] Write `tests/unit/test_penalty_service.py` — test `validate_penalty_input` rejects `+5` for qualifying (only DSQ allowed); test DSQ supersedes prior TIME penalty for same driver in same session; test DSQ sets points_awarded=0 and moves driver to last position

- [X] T034 [P] Write `tests/unit/test_amendment_service.py` — test `disable_amendment_mode` raises `AmendmentModifiedError` when modified_flag=1; test `revert_modification_store` resets entries to season store and clears modified_flag; test `approve_amendment` overwrites season_points_entries with modification_entries in one transaction

- [X] T035 Write `tests/integration/test_results_flow.py` — end-to-end with temp in-memory DB: create server + season + division + points config; attach config; approve season (triggers snapshot); manually invoke `_result_submission_job` logic against test channel mock; assert session_results and driver_session_results rows created; assert DriverStandingsSnapshot and TeamStandingsSnapshot written with correct totals

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Models)**: Depends on Phase 1 — blocks Phases 3–10
- **Phase 3 (US1)**: Depends on Phase 2 — T006/T007/T009/T010 can run in parallel after T002 complete
- **Phase 4 (US2)**: Depends on Phase 2 + Phase 3 (T007 for config selection in wizard) — T011/T012/T013 can run in parallel; T014 depends on T012/T013
- **Phase 5 (US3)**: Depends on Phase 2 — T016 can start after T003/T004; T017 after T003/T004; T018 after T016/T017
- **Phase 6 (US4)**: Depends on T007 (season_points_service) and T009 (results_cog skeleton)
- **Phase 7 (US5)**: Depends on Phase 5 (standings cascade) and Phase 4 (session results saved to DB)
- **Phase 8 (US6)**: Depends on T012/T013 (validation) and Phase 5 (standings cascade)
- **Phase 9 (US7)**: Depends on Phase 5 (standings cascade for approval step)
- **Phase 10 (US8)**: Depends on T018 (post_standings with reserves flag)
- **Phase 11 (Polish)**: Depends on all prior phases

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2 — no story dependencies
- **US2 (P1)**: Depends on US1 services (config selection in wizard)
- **US3 (P1)**: Can run alongside US2 (different files) — depends on Phase 2 models only
- **US4 (P1)**: Depends on US1 (season_points_service.get_season_points_view)
- **US5 (P2)**: Depends on US2 (session results in DB) and US3 (cascade recompute)
- **US6 (P2)**: Depends on US2 (validation helpers) and US3 (cascade recompute)
- **US7 (P2)**: Depends on US3 (cascade recompute across all divisions on approval)
- **US8 (P3)**: Depends on US3 (post_standings receives reserves flag)

### Parallel Opportunities per Phase

**Phase 2 (Models)**: T002, T003, T004, T005 — all parallel  
**Phase 3 (US1)**: T006 ∥ T007 then T008 → T009 → T010  
**Phase 4 (US2)**: T011 ∥ T012 ∥ T013 then T014 → T015  
**Phase 5 (US3)**: T016 ∥ T017 then T018  
**Phase 11**: T028 ∥ T029 ∥ T030 ∥ T031 ∥ T032 ∥ T033 ∥ T034 then T035

---

## Parallel Execution Example — Phase 2 (Models)

```
# All model files are independent — create in parallel
T002: src/models/points_config.py      ─────────────────────┐
T003: src/models/session_result.py     ─────────────────────┤ → Phase 3 begins
T004: src/models/standings_snapshot.py ─────────────────────┤
T005: src/models/amendment_state.py    ─────────────────────┘
```

## Parallel Execution Example — Phase 3 (US1)

```
T006: points_config_service.py ──────────────────────────────┐
T007: season_points_service.py ──────────────────────────────┤ → T008 → T009 → T010
                                                              │
(T008 extends season_cog.py — must follow T007)              │
(T009/T010 create results_cog.py — must follow T006/T007)    ┘
```

---

## Implementation Strategy

**MVP scope**: Complete Phases 1–6 (US1–US4) first. This delivers the full P1 feature set: config management, round submission, results output, and standings — everything a league needs to run a season.

**Increment 2**: Phases 7–8 (US5–US6) add post-race corrections. Deliver after MVP is tested end-to-end.

**Increment 3**: Phase 9 (US7) adds mid-season scoring amendments — complex but isolated to the amendment workflow. Deliver after Increment 2 is stable.

**Increment 4**: Phase 10 (US8) is a simple toggle with no structural dependencies. Deliver last.

**Phase 11** (tests + bot registration) runs in parallel with each increment's final integration.
