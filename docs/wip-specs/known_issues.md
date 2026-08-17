# Known issues

Defects and oddities found by reading the implementation, each verified against the source at the time of writing. This document records **what is wrong**, not what shall be done about it — a fix is a decision for the author, and an entry here is not a licence to change behaviour.

Every entry states what a league actually sees, because several of these are invisible from the code alone and only surface as a confused league manager.

Found on 2026-08-17 while writing the weather module how-to guide, unless stated otherwise.

## Weather

**The configured phase deadlines are ignored when a round is amended.**
- `src/services/amendment_service.py` derives the three horizons from the literals 5 days, 2 days and 2 hours, and calls `schedule_round` without passing the server's configured values, so the scheduler falls back to its own defaults.
- A league that configured, say, a 7-day Phase 1 keeps it for every round except any round it amends, which silently reverts to 5 / 2 / 2 for the rest of its life.
- Nothing reports the reversion. `/season review` continues to show the configured values, which are no longer the ones in force for that round.

**The configured phase deadlines are ignored when the bot restarts.**
- `_recover_missed_phases` in `src/bot.py` hard-codes the same 5 / 2 / 2 when deciding which phases are overdue.
- A league with a Phase 1 deadline longer than 5 days therefore has a window in which a restart does not recover a phase that is genuinely overdue; one with a shorter deadline can have a phase fired earlier than it should be.
- `_catchup_and_schedule_weather` in `src/cogs/module_cog.py` reads the stored configuration correctly, so the two recovery paths disagree with each other.

**Forecast messages state the default horizons regardless of configuration.**
- `phase1_message`, `phase2_message` and `phase3_message` in `src/utils/message_builder.py` carry the literal wording "(5 days out)", "(2 days out)" and "(2 hours out)".
- The forecast is published at the configured time; only its self-description is wrong. A league that sets a 7-day Phase 1 posts a forecast seven days out that calls itself five.

**Amending a round re-runs the weather phases even when the weather module is disabled.**
- In `amend_round`, the weather-enabled check guards only the rescheduling step. The step that re-runs overdue phases is not guarded and runs for any non-Mystery round.
- A league with weather switched off can therefore have a forecast computed, recorded and posted to a division's forecast channel by amending a round.

**Amending more than one field of a round performs the whole amendment once per field.**
- The confirmation view in `src/cogs/season_cog.py` loops over the amended fields and calls `amend_round` for each.
- Amending a round's track and time together posts the invalidation notice twice and re-runs the phases twice, the second run drawing fresh weather over the first. A league sees duplicate notices and, where a phase had already run, two different forecasts in quick succession.

**Three division channel commands are not restricted to administrators.**
- `/division weather-channel`, `/division results-channel` and `/division standings-channel` carry only the interaction-channel and interaction-role guard. `/division verdicts-channel`, `rsvp-channel`, `attendance-channel`, `lineup-channel` and `calendar-channel` additionally require Manage Server.
- Anyone holding the interaction role can repoint a division's forecast, results or standings channel.
- The README documents `/division weather-channel` as *Access: Trusted admin*, which is the access the other five have and this one does not. Either the code or the README is wrong; which one is a decision, not an oversight to be corrected silently.

**There is no way to read back the phase deadlines on their own.**
- No `/weather config view` exists. `/season review` is the only surface that displays the three values, so a league between seasons must run a season-scoped command to check a server-scoped setting.

**Rain probability cannot be tuned, and the removal is recent enough to mislead.**
- `/track config`, `/track reset` and `/track info` were removed along with the `track_rpc_params` table in migration 029. Only `/track list` remains, and μ and σ are seeded per circuit.
- This is intended behaviour, not a defect, but it was documented as configurable in the README until 2026-08-17 and may still be assumed by anyone working from memory or from `specs/`.

## Core setup and access

Found on 2026-08-17 while writing the core configuration how-to guide.

**The bot answers out-of-channel commands, which the constitution forbids.**
- Principle I states that the bot "MUST reject out-of-channel commands **silently** (no response)". `channel_guard` in `src/utils/channel_guard.py` logs a warning and replies "⛔ This command can only be used in the configured interaction channel." Its own module docstring and its inline comment both claim the silent behaviour it does not implement.
- A member who types a command in the wrong channel is told the bot exists and where its command channel is, rather than being ignored — which is the disclosure the principle was written to prevent.
- Either the code or Principle I is wrong. Which is a decision, not an oversight to correct silently, and the constitution may only be amended via `/speckit-constitution`. Note that `docs/how-to/configuring-the-core-bot.md` documents the observed behaviour, so a fix in either direction obliges a change there.

**`/bot-init` promises default teams it does not create.**
- `src/cogs/init_cog.py` calls `seed_default_teams_if_empty` under the comment "Seed default F1 teams + Reserve". The method inserts the Reserve team and nothing else.
- The behaviour is defensible — a league names its own teams — but a reader of the cog is told a full grid ships, and does not.

## Season lifecycle

**The automatic season-end path is dead code that six tests still exercise.**
- Nothing in `src/` calls `check_and_schedule_season_end`. `_recover_season_end_jobs` in `src/bot.py` is an explicit no-op documented as such, and `/season complete` calls `execute_season_end` directly.
- The function and its seven-days-after-the-last-round scheduling logic are therefore unreachable in production, while `tests/unit/test_season_end_service.py` covers them in six tests that pass. The suite reports coverage for a path that cannot run, which is the opposite of what coverage is read for.
- This one has already misled documentation: the README described automatic completion until 2026-08-17.

## Data model

**`save_server_config` silently discards two of the fields it is given.**
- The `ON CONFLICT` clause in `src/services/config_service.py` updates `interaction_role_id`, `interaction_channel_id`, `log_channel_id` and `test_mode_active` only. `weather_module_enabled` and `signup_module_enabled` are bound into the `INSERT` but never into the update, so on an existing row those two are kept at their stored values whatever the caller passed.
- Latent rather than live: `module_cog` writes those columns with its own `UPDATE` statements and does not go through this method. It also means `/bot-init force:True` preserves module state — the right outcome, reached by omission rather than by intent.

**`ServerConfig.previous_season_number` never round-trips.**
- `get_server_config` does not select the column, and the returned dataclass therefore always carries the default `0`. `increment_previous_season_number` in `season_service` writes it with raw SQL.
- Nothing reads the field off the dataclass today, so nothing is wrong yet; anything that starts to will read `0` for every server regardless of the stored value.

**Module state has two homes.**
- `ServerConfig` carries `weather_module_enabled` and `signup_module_enabled`. The results, attendance and images modules are not on it at all — their state is reached through `module_service`.
- Enablement read from a `ServerConfig` is therefore a partial answer, and correct only for the two oldest modules.

**`track_records` and `lap_records` are created and never used.**
- Migration `029_track_data_expansion.sql` creates both tables, annotated "populated by a future increment". Neither has a single reference anywhere in `src/`.
- The per-tier track and lap records described in `docs/wip-specs/other_changes.md` therefore exist as schema only. No command writes one and no surface displays one.

**Two timestamps are written with the deprecated `datetime.utcnow()`.**
- `src/services/penalty_service.py:368` and `src/services/result_submission_service.py:660`. Both produce a naive datetime, and both account for the bulk of the suite's fourteen `DeprecationWarning`s.
- The value is stored as an ISO string, so nothing misbehaves today, but the call is scheduled for removal and the naivety is inconsistent with the timezone-aware datetimes used elsewhere.

## Test coverage

**The weather module's own configuration and pipeline are untested.**
- No test references `weather_config_service`, `validate_ordering`, `set_phase_1_days`, `run_phase1`, `run_phase2`, `run_phase3` or `WeatherCog`. `schedule_round` is referenced only by `tests/unit/test_mystery_notice.py`.
- The ordering invariant, the minimum of 1, the active-season refusal and the three phase draws therefore have no automated cover at all, while `math_utils`, `message_builder`, `forecast_cleanup`, `mystery_notice` and the image weather path are all well covered.
- This cuts against the standing rule that every implementation task carries the unit test that covers it.

**Two tests in `tests/unit/test_forecast_cleanup.py` are named for behaviour that no longer exists.**
- `test_test_mode_suppresses_delete_keeps_row` and `test_delete_forecast_message_skips_in_test_mode` both assert that deletion **does** happen in test mode, and their docstrings say so.
- The names are left over from the removed test-mode guard. They pass, so nothing fails, but a reader scanning test names is told the opposite of what the suite checks.

## Behaviour worth knowing rather than fixing

These are deliberate, or at least consistent, but are surprising enough to be mistaken for defects.

- **Disabling test mode deletes every stored forecast message for the server**, not only those posted while test mode was active. `flush_pending_deletions` iterates all rows for the server. On a season already running this removes forecasts drivers were reading, and only the next phase restores anything.
- **Disabling test mode also deletes every fake driver on the server**, across all divisions, via `clear_all_test_drivers`. There is no confirmation and no per-division scope, so toggling the flag to check something destroys a roster that may have taken many `/test-mode roster add` calls to build.
- **Enabling test mode seeds points configurations onto the current season**, creating and attaching **Standard** and **Half Points** where none are attached. It is idempotent, but it means a season can acquire a points configuration as a side effect of a command that appears only to flip a flag.
- **A division created by `/division duplicate` does not inherit the source division's forecast channel.** Nothing warns at the time; it surfaces later as a season that refuses to approve.
- **Cancelling a round does not delete forecasts already posted for it.** The division is told no further forecast will follow, but the standing forecast remains.
- **Disabling the weather module clears nothing** beyond cancelling scheduled jobs — channels, deadlines, recorded phase results and posted messages all survive. This differs from the general rule stated for `/module disable`.
