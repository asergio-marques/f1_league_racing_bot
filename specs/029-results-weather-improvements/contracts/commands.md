# Command Contracts: Results Resubmission & Weather Phase Configurability

**Feature**: `029-results-weather-improvements`  
**Date**: 2026-04-02  
**Convention**: All commands follow `/domain action [subaction] [params]` per Bot Behavior Standards.

---

## New Button: "🔄 Resubmit Initial Results"

Not a slash command — a Discord UI button inside the `PenaltyReviewView`.

| Attribute | Value |
|---|---|
| Label | `🔄 Resubmit Initial Results` |
| Style | `ButtonStyle.danger` |
| `custom_id` | `pw_resubmit` |
| Row | 0 (alongside Add Penalty / No Penalties / Approve) |
| Access | League managers only (same gate as other `PenaltyReviewView` buttons) |
| Availability | Present from when `PenaltyReviewView` is first posted until the penalty wizard is approved/finalised. **Not** present in `AppealsReviewView`. |

**Interaction flow**:
1. Bot defers the interaction (ephemeral).
2. Staged penalty discard audit log entry is written.
3. Existing `DriverSessionResult` rows for the round are superseded.
4. `session_results` rows are reset / deleted.
5. `round_submission_channels` columns `in_penalty_review` and `results_posted` reset to 0.
6. Bot posts a resubmission notice to the submission channel.
7. First-session collection prompt is re-posted (exactly as on original submission start).
8. On re-submission completion, `enter_penalty_state` is called with `is_resubmission=True`, which triggers posting with label `"Provisional Results (amended)"`.

**Error cases**:
- If `state is None` (bot restarted): bot sends ephemeral "⚠️ The bot was restarted. Please wait for the penalty prompt to refresh."
- If actor is not a league manager: ephemeral "⛔ Only league managers can interact with the penalty review."

---

## New Slash Commands: `/weather config phase-*-deadline`

All three commands live under a new subgroup: `/weather config`.

> **New cog required**: A `WeatherCog` (or equivalent `weather_group` added to an existing cog) must expose the `/weather` top-level group. Given that no `/weather` group currently exists, a new `src/cogs/weather_cog.py` is needed. The three `phase-N-deadline` commands are subcommands of a `config` subgroup within it.

### `/weather config phase-1-deadline <days>`

| Attribute | Value |
|---|---|
| Group | `/weather config` |
| Parameter | `days: int` — positive integer, number of days |
| Access | Tier-2 admin (league manager role, Principle I) |
| Response | Ephemeral |
| Module gate | Weather module must be enabled |

**Pre-conditions / validation** (checked in order, first failure wins):
1. Weather module is enabled — error: "❌ The weather module is not enabled."
2. No ACTIVE season — error: "❌ Phase deadline configuration cannot be changed while a season is active."
3. `days >= 1` — error: "❌ Phase 1 deadline must be at least 1 day."
4. Ordering rule: `days × 24 > current_phase_2_days × 24` — error: "❌ Phase 1 deadline ({days}d) must be greater than the current Phase 2 deadline ({p2}d). Update Phase 2 first, or choose a value greater than {p2}d."

**On success**:  
- Upsert `weather_pipeline_config.phase_1_days = days`.
- Write audit log entry: actor, change type `WEATHER_CONFIG_PHASE1_DEADLINE`, old value, new value.
- Respond: "✅ Phase 1 deadline set to **{days} day(s)** before round. (Phase 2: {p2}d, Phase 3: {p3}h)"

---

### `/weather config phase-2-deadline <days>`

| Attribute | Value |
|---|---|
| Group | `/weather config` |
| Parameter | `days: int` — positive integer, number of days |
| Access | Tier-2 admin |
| Response | Ephemeral |
| Module gate | Weather module must be enabled |

**Pre-conditions / validation**:
1. Weather module enabled.
2. No ACTIVE season.
3. `days >= 1`.
4. Ordering rule upper bound: `days × 24 < current_phase_1_days × 24` — error: "❌ Phase 2 deadline ({days}d) must be less than the current Phase 1 deadline ({p1}d). Update Phase 1 first, or choose a value less than {p1}d."
5. Ordering rule lower bound: `days × 24 > current_phase_3_hours` — error: "❌ Phase 2 deadline ({days}d = {days×24}h) must be greater than the current Phase 3 deadline ({p3}h). Update Phase 3 first, or choose a value where {days}×24 > {p3}."

**On success**:  
- Upsert `weather_pipeline_config.phase_2_days = days`.
- Write audit log entry.
- Respond: "✅ Phase 2 deadline set to **{days} day(s)** before round. (Phase 1: {p1}d, Phase 3: {p3}h)"

---

### `/weather config phase-3-deadline <hours>`

| Attribute | Value |
|---|---|
| Group | `/weather config` |
| Parameter | `hours: int` — positive integer, number of hours |
| Access | Tier-2 admin |
| Response | Ephemeral |
| Module gate | Weather module must be enabled |

**Pre-conditions / validation**:
1. Weather module enabled.
2. No ACTIVE season.
3. `hours >= 1`.
4. Ordering rule: `hours < current_phase_2_days × 24` — error: "❌ Phase 3 deadline ({hours}h) must be less than the current Phase 2 deadline ({p2}d = {p2×24}h). Update Phase 2 first, or choose a value less than {p2×24}h."

**On success**:  
- Upsert `weather_pipeline_config.phase_3_hours = hours`.
- Write audit log entry.
- Respond: "✅ Phase 3 deadline set to **{hours} hour(s)** before round. (Phase 1: {p1}d, Phase 2: {p2}d)"

---

## Audit Log Change Types (new)

| Change type string | Trigger |
|---|---|
| `WEATHER_CONFIG_PHASE1_DEADLINE` | `/weather config phase-1-deadline` succeeds |
| `WEATHER_CONFIG_PHASE2_DEADLINE` | `/weather config phase-2-deadline` succeeds |
| `WEATHER_CONFIG_PHASE3_DEADLINE` | `/weather config phase-3-deadline` succeeds |
| `RESULTS_RESUBMISSION_STAGED_DISCARD` | "Resubmit Initial Results" button pressed — logs discarded staged penalty count |
| `RESULTS_RESUBMISSION` | Resubmission results validated and accepted — logs before/after session result IDs |
