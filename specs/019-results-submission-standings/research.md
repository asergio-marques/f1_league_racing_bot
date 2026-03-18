# Research: Results & Standings — Points Config, Submission, and Standings

**Feature**: `019-results-submission-standings`  
**Date**: 2026-03-18  
**Status**: Complete — no NEEDS CLARIFICATION items remain

---

## 1. discord.py Multi-Step Wizard Pattern (Submission Channel Flow)

**Decision**: The submission wizard collects session results as raw text messages sent in the transient submission channel, not via slash command modals. After each valid session input, a `discord.ui.View` with config-selection buttons is posted. The wizard state (which session is pending) is held in a per-round in-memory record keyed on `round_id`, persisted at each step so a bot restart can recover.

**Rationale**: The number of result lines per session is variable (up to 20+ drivers) and cannot fit in a modal `TextInput` (Discord limit: 4000 characters per modal, but wizard UX is simpler with a raw message). The existing bot already collects answers via raw channel messages in the signup wizard (`wizard_service.py`), so this pattern is established and consistent.

**Alternatives considered**:
- Slash command with a multiline text parameter: rejected — Discord slash command options do not support multi-line free-text in a reliable way, and the format is too long for a short option field.
- One-driver-per-line slash command loop: rejected — too slow; 20+ interactions per session.

**Implementation note**: Use `bot.wait_for("message", check=..., timeout=900)` in an async background task spawned from the APScheduler job, rather than in the cog interaction handler. State persistence key: `session_results.status = 'PENDING_INPUT'` in the DB before awaiting, reset on receipt.

---

## 2. APScheduler DateTrigger at Round Start for Submission Channel

**Decision**: Add a new `results_r{round_id}` job via `DateTrigger` at `round.scheduled_at` (no offset) in `scheduler_service.schedule_round`. The job calls a `_result_submission_job` top-level function (same pattern as `_phase_job`). The job is only registered when the Results & Standings module is enabled; this check happens inside `schedule_round` if a `results_enabled` callback is registered, or unconditionally inside the job itself via a DB read.

**Rationale**: The existing scheduler pattern already uses `DateTrigger` with `replace_existing=True`; adding a parallel job for results is a natural extension. Checking module-enabled inside the job is safer than at schedule-time because module state can change between season approval and round start.

**Alternatives considered**:
- Scheduling only when module is enabled: rejected — schedule_round is called at season approval time; the module enabled state could change before the round fires. A DB-read guard inside the job is more robust.
- A separate APScheduler instance for results jobs: rejected — unnecessary complexity; one scheduler instance, consistent job-ID prefix `results_r`.

**Implementation note**: `cancel_round` must also cancel `results_r{round_id}`. On amendment (round time change), `schedule_round` with `replace_existing=True` handles the re-scheduling automatically.

---

## 3. Discord Channel Creation Adjacent to Results Channel

**Decision**: Create the transient submission channel in the same category as the division's configured results channel. Use `guild.create_text_channel(name=..., category=results_channel.category)`. Name pattern: `results-submission-{division_name_slug}-r{round_number}`.

**Rationale**: Discord's channel API does not support "insert at position N" reliably across clients. Using the same category as the results channel is consistent with how the signup module creates per-driver channels in the same category as the general signup channel (observed in `wizard_service.py`'s channel creation).

**Alternatives considered**:
- Placing after the results channel by position integer: rejected — position values in Discord's API are advisory and not reliably ordered in the client; category membership is sufficient for "adjacent" semantics per the spec assumption.

---

## 4. Batch Standings Recomputation (Cascade from Round N)

**Decision**: When any result is amended or a penalty is applied starting from round R of division D, recompute standings for round R and all subsequent rounds in chronological order. This means: load all `session_results` for the division sorted by round number ≥ R, apply points computation fresh from stored `driver_session_results`, write new `driver_standings_snapshots` and `team_standings_snapshots` rows (replacing existing rows for those rounds via `INSERT OR REPLACE`), then re-post the results and standings messages for all affected rounds in the division's channels.

**Rationale**: Snapshot-based records are independent per round but must be rebuilt from round R forward because each round's snapshot reflects cumulative totals. Replacing (not deleting+inserting) snapshots using `UNIQUE` constraints and `INSERT OR REPLACE` ensures atomicity per round without needing a transaction spanning all rounds.

**Alternatives considered**:
- Delete all snapshots from R onwards and recalculate: equivalent, but `INSERT OR REPLACE` is cleaner and avoids a multi-step delete-then-insert sequence.
- Live calculation without snapshots: rejected — constitution XII mandates snapshots as authoritative historical records.
- Full-season recalculation on every amendment: acceptable but wasteful; starting from round R is sufficient and proportional.

**Implementation note**: Discord message edit is preferred over delete+repost to avoid losing channel history. If the stored message ID for a round's results post is available, edit it; if the message was deleted by an admin, post a fresh one.

---

## 5. Button Interaction Timeout in Submission Wizard

**Decision**: Config-selection Views (one button per attached seasonal config) use `discord.ui.View(timeout=None)` — no timeout — since the submission wizard must wait indefinitely for the admin to choose. The View is `stop()`-ed on button press. A per-round lock prevents concurrent submissions.

**Rationale**: The existing bot uses `timeout=None` for action-row views that must persist across bot restarts (observed in `admin_review_cog.py` approval buttons). Config selection is similarly long-lived.

**Alternatives considered**:
- 15-minute timeout: rejected — league admins may be AFK briefly; a timed-out config selection would leave the submission in a broken half-complete state.
- Auto-selecting the only config if there is exactly one: accepted as an optimisation — if the season has only one attached config, the bot skips the button prompt and selects it automatically.

---

## 6. Points Monotonic Ordering Gate at Season Approval

**Decision**: The gate already partially exists in `season_cog.py` (Gate 2 in `_do_approve`). Extend it to also run a monotonic-ordering check: for each `(season_id, config_name, session_type)` group in `season_points_entries`, load all positions ordered ascending and assert `points[i] >= points[i+1]`. If any group fails, block approval and return a diagnostic listing the config name, session type, and the first violating position pair.

**Rationale**: The constitution (XII, Points Configuration Store) mandates this gate. It is the correct place — at approval time — rather than at config-edit time, because the admin is building a complete picture first and should have the freedom to set positions in any order.

**Alternatives considered**:
- Check at each config-edit command: rejected — inconvenient; intermediate states may be temporarily invalid while an admin is mid-edit.
- Allow zero-gap ties (position N and N+1 equal): allowed — the spec says "non-increasing", so ties are valid. Only strict decreases are a violation (higher position yields fewer points than lower position).

---

## 7. Fastest-Lap Eligibility Rules (DNF vs DNS/DSQ)

**Decision**: DNF driver: `outcome == 'DNF'` → eligible for fastest-lap bonus if `finishing_position <= fl_position_limit` (or no limit set); ineligible for finishing-position points. DNS/DSQ: ineligible for both. CLASSIFIED: eligible for both. This is the constitution's rule (XII, Result Submission, last bullet) which supersedes the source specification's text.

**Rationale**: Documented as an assumption in the spec and grounded in the constitution's authoritative rule. The implementation must check `outcome` separately from `finishing_position_points` vs `fastest_lap_bonus` eligibility.

---

## 8. Session Type Mapping for Endurance Rounds

**Decision**: For Endurance rounds, `Full Qualifying` maps to `FEATURE_QUALIFYING` session type and `Full Race` maps to `FEATURE_RACE` session type for all results-storage, points-config, and standings-counting purposes. Sprint sessions are not collected for Endurance rounds (same rule as Normal).

**Rationale**: Constitution XII, Result Submission: "Endurance: the Full Qualifying session maps to Feature Qualifying; the Full Race session maps to Feature Race for result-type and points-configuration purposes."

---

## 9. Format Validation Approach (Lap Times, Total Times)

**Decision**: Use a single `validate_result_row` function per session type that applies regex-based validation to each field. Reuse the lap-time format rules already established in `signup_module_service.py` and `test_lap_time.py` (formats: `M:ss.mmm`, `ss.mmm`, `H:MM:SS.mmm`). Extend with race-specific formats: absolute time (same formats), delta (`+ss.mmm`, `+M:ss.mmm`, `+H:MM:SS.mmm`), lap gaps (`x Laps`, `+x Laps`). Time-penalties format: same as absolute lap time. The strings `DNS`, `DNF`, `DSQ`, `N/A` are accepted literal values in their respective fields.

**Rationale**: The lap-time parsing logic is already unit-tested and battle-hardened in this codebase. Extending it for race-specific formats is lower-risk than a new parser.

**Alternatives considered**:
- A third-party parser library: rejected — adds a new dependency for something already implemented in-project; overkill for the simple formats in scope.

---

## 10. Post-Steward Fields (Future-Proofing)

**Decision**: Add `post_steward_total_time` and `post_race_time_penalties` columns to `driver_session_results` as nullable TEXT columns from day one, always `NULL` unless a penalty has been applied. These are never used in the current feature's computation (as per the spec: "It is always presumed that the Total Time column already includes the time noted in the Race Time Penalties").

**Rationale**: The source specification explicitly calls these out: "In preparation of further functionality, the data tables on which race results are saved shall possess two extra columns". Leaving them out now would create a schema migration with data implications later.
