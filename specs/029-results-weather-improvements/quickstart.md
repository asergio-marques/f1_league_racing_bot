# Developer Quickstart: Results Resubmission & Weather Phase Configurability

**Feature**: `029-results-weather-improvements`  
**Date**: 2026-04-02

---

## What's changing

Two independent sets of changes in this feature:

1. **Results hotfix resubmission** — a new button in the penalty wizard that lets a tier-2 admin re-enter a session's results from scratch, discarding any staged penalties, without leaving the submission channel.
2. **Configurable weather phase deadlines** — three new `/weather config phase-N-deadline` commands that let league managers set the T−N horizons for each weather phase, replacing the hardcoded 5d/2d/2h values.

---

## Working areas

```
src/
├── cogs/
│   ├── weather_cog.py          ← NEW: /weather command group
│   └── results_cog.py          ← no change needed (resubmit is wizard-driven)
├── models/
│   └── weather_config.py       ← NEW: WeatherPipelineConfig dataclass
├── services/
│   ├── penalty_wizard.py       ← add pw_resubmit button + callback
│   ├── result_submission_service.py  ← add enter_resubmit_flow()
│   ├── scheduler_service.py    ← accept phase deadline params in schedule_round()
│   └── weather_config_service.py    ← NEW: CRUD for weather_pipeline_config
├── db/
│   └── migrations/
│       └── 028_weather_pipeline_config.sql  ← NEW
tests/
├── unit/
│   ├── test_penalty_wizard.py           ← extend: pw_resubmit button contract
│   ├── test_result_submission_service.py ← extend: resubmit flow helpers
│   ├── test_weather_config_service.py   ← NEW: CRUD + ordering invariant
│   └── test_scheduler_service.py        ← NEW or extend: configurable horizons
└── integration/
    ├── test_penalty_flow.py             ← extend: resubmit path
    └── test_weather_config_flow.py      ← NEW: deadline commands + active-season gate
```

---

## Implementation sequence

### Step 1 — DB migration

Create `src/db/migrations/028_weather_pipeline_config.sql`:

```sql
CREATE TABLE IF NOT EXISTS weather_pipeline_config (
    server_id    INTEGER PRIMARY KEY
                     REFERENCES server_configs(server_id) ON DELETE CASCADE,
    phase_1_days INTEGER NOT NULL DEFAULT 5,
    phase_2_days INTEGER NOT NULL DEFAULT 2,
    phase_3_hours INTEGER NOT NULL DEFAULT 2
);
```

### Step 2 — Model + service

Create `src/models/weather_config.py`:

```python
from dataclasses import dataclass

@dataclass
class WeatherPipelineConfig:
    server_id: int
    phase_1_days: int = 5
    phase_2_days: int = 2
    phase_3_hours: int = 2
```

Create `src/services/weather_config_service.py` with:
- `get_weather_pipeline_config(db_path, server_id) -> WeatherPipelineConfig` — returns row or default-valued instance.
- `set_phase_1_days(db_path, server_id, days)` — upsert + ordering validation.
- `set_phase_2_days(db_path, server_id, days)` — upsert + ordering validation.
- `set_phase_3_hours(db_path, server_id, hours)` — upsert + ordering validation.
- Validation helper: `validate_ordering(p1_days, p2_days, p3_hours) -> str | None` — returns error string or None.

### Step 3 — Scheduler update

In `scheduler_service.py`, change `schedule_round()` signature:

```python
def schedule_round(
    self,
    rnd: Round,
    *,
    phase_1_days: int = 5,
    phase_2_days: int = 2,
    phase_3_hours: int = 2,
) -> None:
```

Replace hardcoded `timedelta(days=5)`, `timedelta(days=2)`, `timedelta(hours=2)` with:

```python
horizons = {
    1: scheduled_at - timedelta(days=phase_1_days),
    2: scheduled_at - timedelta(days=phase_2_days),
    3: scheduled_at - timedelta(hours=phase_3_hours),
}
```

All callers (`_catchup_and_schedule_weather`, restart recovery in `bot.py`) must fetch `WeatherPipelineConfig` and pass the values in. The MYSTERY round notice job hardcodes T−5d independently — this is NOT parameterised by the phase deadline config (it's a separate notice, not a weather phase).

### Step 4 — Weather cog

Create `src/cogs/weather_cog.py` with:

```python
class WeatherCog(commands.Cog):
    weather = app_commands.Group(name="weather", ...)
    config_group = app_commands.Group(name="config", parent=weather, ...)
```

Add three subcommands: `phase_1_deadline`, `phase_2_deadline`, `phase_3_deadline`.  
Each follows the validation sequence in `contracts/commands.md` and calls the matching `weather_config_service` setter.

Register `WeatherCog` in `bot.py` alongside other cogs.

### Step 5 — Penalty wizard resubmit button

In `penalty_wizard.py`:

1. Add `_CID_RESUBMIT = "pw_resubmit"` constant.
2. Add the button to `PenaltyReviewView`:

```python
@discord.ui.button(
    label="🔄 Resubmit Initial Results",
    style=discord.ButtonStyle.danger,
    custom_id=_CID_RESUBMIT,
    row=0,
)
async def resubmit_btn(self, interaction, button):
    ...
```

3. Callback: LM gate → defer → call `enter_resubmit_flow(interaction, self.state)`.

### Step 6 — Resubmit flow

In `result_submission_service.py`, add `enter_resubmit_flow(interaction, state)`:

1. Write `RESULTS_RESUBMISSION_STAGED_DISCARD` audit entry.
2. Clear `state.staged`.
3. Supersede existing `DriverSessionResult` rows (`is_superseded = 1`) and reset / delete `session_results` rows for all sessions of the round.
4. Reset `round_submission_channels`: `in_penalty_review = 0`, `results_posted = 0`.
5. Post "⚠️ Resubmission started" notice to submission channel.
6. Post first-session collection prompt (reuse existing first-session posting code, passing `is_resubmission=True` flag through to `enter_penalty_state` after collection completes).

Modify `enter_penalty_state(... is_resubmission: bool = False)`: when `True`, use `"Provisional Results (amended)"` label for `post_round_results` and `post_standings` calls.

---

## Key invariants to preserve

- **Existing non-resubmit penalty path is unchanged**: the resubmit button is purely additive. All existing `PenaltyReviewView` buttons keep their custom_ids and callbacks.
- **Scheduling defaults are unchanged**: `schedule_round()` defaults to 5/2/2 so existing callers that pass no arguments continue to work correctly.
- **Audit trail is unbroken**: every resubmission event, staged-penalty discard, and deadline change produces an audit entry readable from the log channel.
- **Active-season gate on deadline changes**: `get_active_season()` from `season_service` is used; if not None, command is rejected before any DB write.

---

## Running tests

```bash
# From repo root
python -m pytest tests/ -v
```

Target test files for this feature:
```bash
python -m pytest tests/unit/test_penalty_wizard.py tests/unit/test_weather_config_service.py tests/unit/test_scheduler_service.py tests/integration/test_penalty_flow.py tests/integration/test_weather_config_flow.py -v
```
