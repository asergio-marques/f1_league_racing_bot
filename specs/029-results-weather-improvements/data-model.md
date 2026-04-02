# Data Model: Results Resubmission & Weather Phase Configurability

**Feature**: `029-results-weather-improvements`  
**Date**: 2026-04-02

## Changed Entities

### WeatherPipelineConfig *(new table: `weather_pipeline_config`)*

One row per server. Stores the three configurable phase horizons. Created on first use (upsert on first weather config command or module enable). Owned by the weather module — row is NOT deleted when the module is disabled (deadline preferences are preserved).

| Column | Type | Default | Notes |
|---|---|---|---|
| `server_id` | INTEGER PK | — | FK → `server_configs(server_id)` ON DELETE CASCADE |
| `phase_1_days` | INTEGER NOT NULL | 5 | T−N days for Phase 1 fire-time |
| `phase_2_days` | INTEGER NOT NULL | 2 | T−N days for Phase 2 fire-time |
| `phase_3_hours` | INTEGER NOT NULL | 2 | T−N hours for Phase 3 fire-time |

**Ordering invariant** (validated before any write): `phase_1_days × 24 > phase_2_days × 24 > phase_3_hours` (strict).

**Schema migration**: `028_weather_pipeline_config.sql`

---

## Modified Code (no schema changes)

### `scheduler_service.py` — `SchedulerService.schedule_round()`

**Change**: `schedule_round` currently hardcodes the three horizons as `timedelta(days=5)`, `timedelta(days=2)`, `timedelta(hours=2)`. This method must accept the per-server deadline values. Two options are viable:

- **Option A (preferred)**: Accept optional `phase_days: tuple[int, int, int] | None = None` and `phase_hours_p3: int | None = None` parameters; use defaults when None. Caller (bot restart recovery and `_catchup_and_schedule_weather`) reads from `weather_pipeline_config` and passes values in.
- **Option B**: Have `schedule_round` do the DB lookup itself (requires `db_path` and `server_id` to be passed).

Option A is preferred: it keeps `schedule_round` synchronous and testable without DB.

**New helper** in `scheduler_service.py` or `config_service.py`: `get_weather_pipeline_config(db_path, server_id) -> WeatherPipelineConfig` — returns config row or default values if absent.

### `penalty_wizard.py` — `PenaltyReviewView`

**Change**: Add a fourth button **"🔄 Resubmit Initial Results"** to `PenaltyReviewView` (row 0 or row 1, after existing static buttons). Button carries a new stable `custom_id` constant (e.g., `pw_resubmit`).

- Button not disabled when `state is None` (restart safety: posts the same "bot was restarted" ephemeral as other buttons when state is None).
- Callback: confirms intent with a brief ephemeral, clears `state.staged`, then calls a new function `enter_resubmit_flow(interaction, state)` in `result_submission_service.py`.

`PenaltyReviewState` gains no new fields — staged list is cleared in-place.

### `result_submission_service.py`

**New async function** `enter_resubmit_flow(interaction, state: PenaltyReviewState) -> None`:

1. Audit-log the staged-penalty discard (actor, session context, count of discarded entries).
2. Clear `state.staged`.
3. Delete the existing `DriverSessionResult` rows for all sessions of the round (set `is_superseded = 1` or delete, consistent with existing supersession pattern), and delete/reset the `session_results` rows so the submission wizard can re-enter from the beginning.
4. Reset `round_submission_channels.in_penalty_review = 0` and `results_posted = 0` for the round.
5. Re-post the first session's collection prompt in the submission channel (same path as the original first-session submission).
6. Post a notice to the submission channel: "⚠️ Results resubmission started. Previous provisional results will be replaced."

**Modification to `enter_penalty_state()`**: After posting the provisional results and standings, add a `label_suffix` mechanism so the results post function can append "(amended)" when called from a resubmit path. Specifically: after resubmission completes, the second call to `enter_penalty_state` is made with a new optional `is_resubmission: bool = False` parameter that causes:
- `results_post_service.post_round_results(... label="Provisional Results (amended)")` 
- `results_post_service.post_standings(... label="Provisional Results (amended)")`

### `results_post_service.py`

No structural changes to entity schema. The `label` parameter already accepts arbitrary strings — passing `"Provisional Results (amended)"` is sufficient. No code change needed to this file for the label; the change is in the caller.

---

## New Entity Summary

| Entity | Table | Migration | Scope |
|---|---|---|---|
| WeatherPipelineConfig | `weather_pipeline_config` | `028_weather_pipeline_config.sql` | Per-server |
