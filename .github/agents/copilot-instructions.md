# f1_league_weather_randomizer_bot Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-03-04

## Active Technologies
- Python 3.13.2 (targets 3.8+) + discord.py 2.7.1 (`app_commands.Choice`, `@command.autocomplete`), aiosqlite ≥ 0.19, APScheduler ≥ 3.10 (003-track-id-autocomplete)
- SQLite via aiosqlite; schema versioned with sequential SQL migration files applied on startup (003-track-id-autocomplete)

- Python 3.13.2 (targets 3.8+) + discord.py 2.7.1, aiosqlite ≥ 0.19, APScheduler ≥ 3.10 (002-test-mode)

## Project Structure

```text
src/
tests/
```

## Commands

cd src [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] pytest [ONLY COMMANDS FOR ACTIVE TECHNOLOGIES][ONLY COMMANDS FOR ACTIVE TECHNOLOGIES] ruff check .

## Code Style

Python 3.13.2 (targets 3.8+): Follow standard conventions

## Recent Changes
- 003-track-id-autocomplete: Added Python 3.13.2 (targets 3.8+) + discord.py 2.7.1 (`app_commands.Choice`, `@command.autocomplete`), aiosqlite ≥ 0.19, APScheduler ≥ 3.10

- 002-test-mode: Added Python 3.13.2 (targets 3.8+) + discord.py 2.7.1, aiosqlite ≥ 0.19, APScheduler ≥ 3.10

<!-- MANUAL ADDITIONS START -->
## Feature 029: Results Resubmission & Weather Phase Configurability

### New files
- `src/cogs/weather_cog.py` — `/weather` app_commands.Group with nested `config` subgroup; three `phase-N-deadline` subcommands
- `src/models/weather_config.py` — `WeatherPipelineConfig` dataclass (phase_1_days, phase_2_days, phase_3_hours)
- `src/services/weather_config_service.py` — CRUD for `weather_pipeline_config`; ordering validation helper
- `src/db/migrations/028_weather_pipeline_config.sql` — adds `weather_pipeline_config` table
- `tests/unit/test_weather_config_service.py`
- `tests/integration/test_weather_config_flow.py`

### Modified files
- `src/services/penalty_wizard.py` — add `pw_resubmit` button + `_CID_RESUBMIT` constant to `PenaltyReviewView`
- `src/services/result_submission_service.py` — add `enter_resubmit_flow()`; add `is_resubmission` param to `enter_penalty_state()`
- `src/services/scheduler_service.py` — `schedule_round()` accepts `phase_1_days`, `phase_2_days`, `phase_3_hours` kwargs (defaults 5/2/2)
- `src/bot.py` — register `WeatherCog`; pass `WeatherPipelineConfig` values to `schedule_round()` calls

### Key rules for this feature
- Ordering invariant for phase deadlines: `phase_1_days × 24 > phase_2_days × 24 > phase_3_hours` (strict). Validated before any DB write; reject with explanation naming the conflicting current value.
- Phase deadline commands are rejected if `season_service.get_active_season()` returns non-None.
- `schedule_round()` must keep existing behaviour when called without phase kwargs (default values = 5/2/2).
- `pw_resubmit` button: same LM auth gate as other `PenaltyReviewView` buttons; state=None safety required.
- Resubmit flow supersedes existing `DriverSessionResult` rows (`is_superseded = 1`) before re-entry; does NOT delete audit log entries.
- Amended provisional results label: `"Provisional Results (amended)"` — passed via existing `label` param to `post_round_results` and `post_standings`.
<!-- MANUAL ADDITIONS END -->
