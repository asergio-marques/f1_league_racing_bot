# Research: Results Resubmission & Weather Phase Configurability

**Feature**: `029-results-weather-improvements`  
**Date**: 2026-04-02

---

## Research Tasks & Findings

### R-001: Penalty wizard state and "staged" penalty lifecycle

**Decision**: "Staged penalties" are entries in `PenaltyReviewState.staged` (an in-memory `list[StagedPenalty]`) that have not yet been passed to `finalize_penalty_review`. No `PenaltyRecord` rows exist until `finalize_penalty_review` commits them. Discarding staged penalties = clearing this list.  
**Rationale**: Confirmed by reading `penalty_wizard.py`. `StagedPenalty` objects live only in the wizard state; the DB is only written in `finalize_penalty_review` via `penalty_service.apply_penalties`.  
**Alternatives considered**: Checking for uncommitted `PenaltyRecord` rows in the DB — rejected; the wizard never writes partial rows.

---

### R-002: How provisional results are posted and what label is used

**Decision**: `enter_penalty_state()` calls `results_post_service.post_round_results(... label="Provisional Results")` and `results_post_service.post_standings(... label="Provisional Results")`. The `label` parameter is already a free string accepted by both functions. Passing `"Provisional Results (amended)"` is sufficient — no structural change to `results_post_service.py`.  
**Rationale**: `post_round_results` embeds the `label` string into the message heading. The `_label_from_status` mapping is only used on re-reads from DB (standings sync); the in-wizard path always passes the label directly.  
**Alternatives considered**: A new `result_status` DB value (`PROVISIONAL_AMENDED`) — rejected: overkill for a display-only distinction; the DB row's `result_status` remains `PROVISIONAL` until `finalize_penalty_review` moves it to `POST_RACE_PENALTY`.

---

### R-003: How to reset state for re-entry after resubmission

**Decision**: To allow the session collection wizard to re-run, the existing `session_results` and `driver_session_results` rows for the round must be superseded (`is_superseded = 1` on `driver_session_results`) and the `session_results` rows deleted (or status reset to `ACTIVE` with no children). The `round_submission_channels` row must have `in_penalty_review = 0` and `results_posted = 0` reset so the recovery logic does not re-post as a penalty-review orphan.  
**Rationale**: The submission wizard is driven by checking which sessions have been submitted (`SELECT ... FROM session_results WHERE round_id = ?`). Deleting/resetting these rows allows the wizard to restart from session 1.  
**Alternatives considered**: Keeping session_results rows with a `SUPERSEDED` status and filtering them — rejected: adds complexity to the submission query path; simpler to delete and start clean.

---

### R-004: Where phase horizons are hardcoded and how to parameterise

**Decision**: Phase horizons are hardcoded in `scheduler_service.py:SchedulerService.schedule_round()` as `timedelta(days=5)`, `timedelta(days=2)`, `timedelta(hours=2)`. The method signature is extended with `phase_1_days: int = 5`, `phase_2_days: int = 2`, `phase_3_hours: int = 2` keyword arguments. All callers pass the values fetched from `WeatherPipelineConfig`; callers that can omit them continue to work via the defaults.  
**Rationale**: This is the sole scheduling site. Keeping the method synchronous avoids the need to make it async just for a DB lookup. The MYSTERY round notice job (`timedelta(days=5)`) is a separate hardcoded horizon — it is not a weather phase and is not parameterised.  
**Alternatives considered**: Injecting `WeatherPipelineConfig` into `SchedulerService.__init__` — rejected: config is per-server but `schedule_round` is called per-round across multiple servers.

---

### R-005: Where to place the new `/weather config` commands

**Decision**: New `src/cogs/weather_cog.py` with a top-level `/weather` app_commands Group and a nested `config` subgroup. `WeatherCog` is registered in `bot.py`.  
**Rationale**: No `/weather` group exists anywhere in the codebase. Module enable/disable lives in `module_cog.py` under `/module`; weather-specific configuration belongs under `/weather`, not `/module`. Adding to an existing cog would mix unrelated concerns.  
**Alternatives considered**: Adding weather config commands to `module_cog.py` — rejected; violates the command grouping principle (same domain, different group).

---

### R-006: Weather pipeline config storage pattern

**Decision**: New table `weather_pipeline_config` (one row per server), separate from `server_configs`. Ownable by the weather module. Row created (upsert) on first deadline command if not already present. Row is NOT deleted when the module is disabled (deadline preferences are settings, not runtime state).  
**Rationale**: Mirrors the pattern of `signup_module_config` and `signup_module_settings` — module-specific config gets its own table. Avoids polluting `server_configs` with module-specific columns.  
**Alternatives considered**: Adding columns to `server_configs` — rejected: incorrect ownership (server config owns core bot setup, not module preferences).

---

### R-007: Active-season gate implementation

**Decision**: Use `bot.season_service.get_active_season(server_id)` (already available on `bot`). If the returned value is not `None`, reject the command with a clear error before any DB write.  
**Rationale**: `get_active_season` is the established API for checking `status = 'ACTIVE'`. All new deadline commands call this gate first, after the module-enabled check.  
**Alternatives considered**: Querying the DB directly in the cog — rejected: duplicates logic already encapsulated in `SeasonService`.

---

## NEEDS CLARIFICATION — resolved

All unknowns resolved. No open items.
