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

**Disabling the weather module cancels every scheduled job for the season, not only the weather ones.**
- `cancel_all_weather_for_server` in `src/services/scheduler_service.py:517` selects every round of a server's ACTIVE and SETUP seasons and calls `cancel_round` on each. `cancel_round` at `:501` removes every job carrying that `round_id` — its docstring says so explicitly — which is the forecast cleanup, the result-submission job and all three RSVP jobs alongside the three weather phases.
- A league that turns weather off mid-season also stops collecting results and asking for check-ins, for every round still to come, with no warning and nothing in the reply beyond "All scheduled weather jobs have been cancelled". Nothing recreates those jobs except `/season approve`.
- Same root cause as the `amend_round` entries under Attendance and Results & standings: `cancel_round` is round-scoped where every caller wants it job-kind-scoped. This is its third trigger, and the widest, because it applies to every remaining round at once rather than to one.

**There is no way to read back the phase deadlines on their own.**
- No `/weather config view` exists. `/season review` is the only surface that displays the three values, so a league between seasons must run a season-scoped command to check a server-scoped setting.

**Rain probability cannot be tuned, and the removal is recent enough to mislead.**
- `/track config`, `/track reset` and `/track info` were removed along with the `track_rpc_params` table in migration 029. Only `/track list` remains, and μ and σ are seeded per circuit.
- This is intended behaviour, not a defect, but it was documented as configurable in the README until 2026-08-17 and may still be assumed by anyone working from memory or from `specs/`.

## Attendance

Found on 2026-08-17 while writing the attendance module how-to guide.

**`/attendance config rsvp-absent-penalty` fails every time it is run.**
- `config_rsvp_absent_penalty` in `src/cogs/attendance_cog.py:262` calls `attendance_service.update_rsvp_absent_penalty`. No such method exists — `AttendanceService` defines `update_no_rsvp_penalty`, `update_absent_penalty` and `update_no_show_penalty` only. The command defers ephemerally and then raises `AttributeError`.
- A league sees the interaction fail to respond. The penalty a driver pays for accepting a check-in and then not appearing is therefore fixed at its enable-time default of 1 and cannot be changed by any command. `/attendance config show` continues to display it, so the value shown is correct but unreachable.
- The README documented this command as `/attendance config no-show-penalty` until 2026-08-17, a name that has never existed in the code. The service method it should be calling is `update_no_show_penalty`, and the column is `no_show_penalty` — the command name is the only place `rsvp_absent` appears.

**Amending a round destroys its check-in and never rebuilds it.**
- `amend_round` in `src/services/amendment_service.py:135` calls `scheduler_service.cancel_round`, which removes *every* job carrying that `round_id` — the RSVP notice, last-notice and deadline included. Only the weather jobs are rescheduled afterwards; `schedule_attendance_round` is called from `src/cogs/season_cog.py:3617` at `/season approve` and nowhere else.
- A league that moves a round's date, changes its circuit or changes its format loses that round's check-in call permanently. No call means no `driver_round_attendance` rows, so the round asks nobody anything and charges nobody — it is recorded as perfect attendance for the whole division.
- Nothing reports it. The invalidation notice a league does see is about the forecast, and says nothing about the check-in.

**A season approved inside the notice window silently skips that round's check-in.**
- `schedule_attendance_round` only adds a job whose fire time is still in the future, logging "Skipping … fire time is in the past" for the rest.
- Approving on the Thursday with the default 5-day notice therefore leaves round 1 with no call, no reminder and no deadline, and the same free-pass outcome as above. The weather pipeline handles the equivalent case by catching up and firing overdue phases immediately; attendance does not.
- `/season approve` succeeds with no warning, and `/season review` shows nothing amiss.

**There is no way to post a check-in call by hand.**
- The three RSVP entry points are scheduler callbacks wired in `src/bot.py:144-158` and the test-mode `advance` phases. No command posts a call for a nominated round.
- Every skipped or failed call above is therefore terminal for that round. `_report_call_failure` tells the league manager to post it again; there is no command that does so.

## Signup

Found on 2026-08-17 while writing the signup module how-to guide.

**`/signup config channel` raises on every invocation and configures nothing.**
- `config_channel` in `src/cogs/signup_cog.py:691-695` is a deprecated alias whose body is `await self.signup_channel(interaction, channel)`. Inside a cog, `self.signup_channel` resolves to the `app_commands.Command` object, and `app_commands.Command` defines no `__call__` anywhere in its MRO on the installed discord.py 2.7.1. The command raises `TypeError: 'Command' object is not callable`.
- A league sees the interaction fail. The sibling alias `/signup config roles` has a body of its own and does work, so one half of the deprecated pair functions and the other does not — and the README documented the broken half as the way to set the channel until 2026-08-17.

**`/signup config roles` is not equivalent to the two commands it was deprecated in favour of.**
- `config_roles` in `src/cogs/signup_cog.py:704` writes `base_role_id` and `signed_up_role_id` and stops. `signup_base_role` at `:882` additionally removes the outgoing base role's overwrite on the signup channel and applies the incoming one — `view_channel=True`, `send_messages=False`, `use_application_commands=True`.
- A league that sets its roles through the deprecated alias gets a base role that cannot see the signup channel. Nothing reports it; the reply is "✅ Signup roles configured." and the failure surfaces later as drivers who cannot find the Sign Up button.
- The two are also gated differently: `config_roles` carries `admin_only` (Manage Server) where `signup_base_role` and `signup_complete_role` carry `server_admin_only` (Administrator). The deprecated alias is the lower bar for the same setting.

**An armed auto-close timer cannot be cancelled.**
- `/signup close` refuses while `close_at` is set and directs the caller to `/signup cancel-timer` (`src/cogs/signup_cog.py:1453`). That string is the only occurrence of `cancel-timer` in `src/`; no such command is registered.
- A league that passes `close_time` to `/signup open` therefore has no way to close the window early. The only escapes are waiting for the timer to fire or `/module disable signup`, which clears the signup channel and both roles.

**Availability slot IDs are list positions, not identifiers, and removing one silently repoints stored answers.**
- `get_slots` in `src/services/signup_module_service.py:142-164` numbers slots `1..N` in chronological order on every read, and `_resequence_slots` (`:215-229`) rewrites the stored column on every add and remove. The dataclass comment in `src/models/signup_module.py:69` claims sequence IDs are "never reused after removal", and the `/signup time-slot remove` parameter is described as a "Stable sequence ID"; neither is true.
- `signup_records.availability_slot_ids` stores those positions. Removing a slot shifts every later slot down one, so a driver recorded as available at `#3` is thereafter read as available at whatever now occupies `#3`. Nothing warns, and `/signup unassigned export` presents the shifted values as fact.
- Edits are blocked only while the window is open, so the damaging case — editing between windows, with signup records already stored — is fully permitted.

**`/module disable signup` reports a clearance it does not perform.**
- `delete_config` in `src/services/signup_module_service.py:86-92` deletes the `signup_module_config` row only. The comment at `src/cogs/module_cog.py:778` claims the delete cascades to settings and slots, but `signup_module_settings` and `signup_availability_slots` carry foreign keys to `server_configs`, not to the config row.
- The reply is "✅ Signup module disabled. All signup configuration has been cleared." In fact the time slots, the three question toggles, every `signup_records` row and every `signup_wizard_records` row survive. Only the channel and the two roles are cleared. Re-enabling restores the old slots and toggles, which is convenient but is not what the message describes.

**The close confirmation counts more drivers than it transitions.**
- `signup_close` in `src/cogs/signup_cog.py:1459-1471` lists drivers in `PENDING_SIGNUP_COMPLETION`, `PENDING_ADMIN_APPROVAL` and `PENDING_DRIVER_CORRECTION`, and warns that closing "will transition all in-progress drivers to **Not Signed Up**". `execute_forced_close` in `src/cogs/module_cog.py:47-51` transitions `PENDING_SIGNUP_COMPLETION` only, which is what spec 028 FR-002/003 requires.
- The retention is correct and the warning is wrong. A league manager is told they are about to discard drivers who are merely awaiting their approval, and may cancel a close they had no reason to avoid. Those drivers also receive no closure notice in their wizard channels, since only the transitioned ones are messaged.

**A restart during the correction-parameter window strands the driver.**
- The five-minute window opened by **Request Changes** is an in-memory `asyncio.sleep` task (`src/services/wizard_service.py:1597-1602`), and the reason-capture map in `src/cogs/admin_review_cog.py:23` is an in-memory dict. The wizard record is `UNENGAGED` in this state, so `recover_wizards` re-arms nothing.
- A driver caught by a restart in `AWAITING_CORRECTION_PARAMETER` waits indefinitely. No admin command returns them to the review queue; their own **Cancel Signup** button is the only exit.

## Results & standings

Found on 2026-08-17 while writing the results module how-to guide.

**Approving a mid-season points amendment recalculates the standings but reposts nothing.**
- `repost_round_results` in `src/services/results_post_service.py:729` takes `label` as a required positional parameter. `approve_amendment` calls it at `src/services/amendment_service.py:549` with four positional arguments and `bot=` only, so every call raises `TypeError: missing a required argument: 'label'`. The call sits inside a `try/except Exception` that logs and moves to the next round.
- `cascade_recompute_from_round` runs first and succeeds, so the new points **are** written to every standings snapshot. Nothing in Discord changes. A league that approves an amendment is told "All standings recomputed and reposted", sees its results and standings channels still showing the old points, and finds one traceback per round in the log channel.
- `/results rounds sync` and `/results standings sync` per division are the recovery, and they are not mentioned by the reply.
- **This breaches the constitution, not merely the wip-spec.** The Amendment & Penalty section states that on approval "all affected results and standings MUST be reposted", and separately that standings for an affected round and all subsequent rounds "MUST be recomputed and reposted atomically". Half of that is happening.
- The same call is made with the same omission at `src/services/penalty_service.py:459`, reachable only when `apply_penalties` is called without `_skip_post=True`. Both production callers pass it, and the integration tests that do not use a fake bot whose `get_guild` returns `None`, so that one is latent and untested rather than live.

**The points-ordering gate at `/season approve` cannot fire on a first approval.**
- `validate_monotonic_ordering` in `src/services/season_points_service.py:109` reads `season_points_entries`, and `/season approve` calls it at `src/cogs/season_cog.py:3480`. The only thing that populates that table for a season being approved is `snapshot_configs_to_season`, called a hundred lines later at `:3583`. The other two writers are `approve_amendment` and `ensure_test_configs`, neither of which runs on the path to a first approval.
- The gate therefore inspects an empty table, finds no violations and passes. A league can approve a season whose second place is worth more than its first, and score a whole championship on it; the refusal that was meant to catch it never comes.
- It does fire on a season that has already been approved once and re-approved, and on one that acquired entries through test mode, which is why the check is not obviously dead.
- The README and the results how-to both described the gate as the safety net for a wrongly built table until 2026-08-18.

**A points configuration may be attached to a season without existing, and the season then refuses to approve with no message at all.**
- `attach_config` in `src/services/season_points_service.py:28` inserts into `season_points_links` without checking that `config_name` is present in `points_config_store`. `remove_config` in `src/services/points_config_service.py:45` deletes the store row without clearing any link to it.
- The R&S approval gate counts link rows, so it passes. `snapshot_configs_to_season` then calls `points_config_service.get_config_entries`, which raises `ConfigNotFoundError`, and `src/cogs/season_cog.py:3583` does not catch it. There is no `on_app_command_error` handler and no `tree.error` anywhere in `src/`, so the already-deferred interaction simply never receives a follow-up.
- A league that mistypes `/results config append`, or removes a configuration still attached, sees `/season approve` do nothing whatever — no error, no reason, and the season left in SETUP. `/season review` continues to list the phantom name as attached.

**Amending a round destroys its results submission and only rebuilds it for weather-enabled servers.**
- `amend_round` in `src/services/amendment_service.py:135` calls `scheduler_service.cancel_round`, which removes every job carrying that `round_id`, the `results_*` job included. The re-scheduling call at `:156` is guarded by `is_weather_enabled`, and `schedule_round` is the only thing that recreates a results job outside `/season approve`.
- A league running results with weather **off** loses that round's submission channel permanently: nothing opens at the scheduled time, the round is never scored, and it silently sits out the championship. With weather on the job is restored, except for a MYSTERY round whose T-5 has already passed, which is excluded by the condition at `:155`.
- Nothing reports it. This is the same root cause as the attendance entry above; the results job is a second casualty of the same unguarded `cancel_round`.

**A restart mid-submission discards every session already submitted for that round.**
- `_recover_orphaned_submission_channels` in `src/bot.py:511` treats a channel with `in_penalty_review = 0` as a mid-submission orphan: it deletes the round's `session_results` rows and the channel, then re-triggers `run_result_submission_job` from the first session.
- A league three sessions into a sprint weekend when the bot restarts pastes all four again. The log channel reports it, which is the only warning.
- Defensible — a part-collected round has no coherent state to resume into — but the cost falls entirely on the person retyping four classifications, and no partial state is offered back.

**`/round results amend` is a single attempt, and its errors go somewhere the caller is not looking.**
- Every failure path in `round_results_amend` (`src/cogs/season_cog.py:3163`, `:3183`, `:3231`) calls `_cleanup_channel`, deleting the amend channel. The collection loop also has a hard `_AMEND_TIMEOUT_S = 300` at `:3102`, after which the channel is deleted with no message in it.
- Validation errors, FL-override errors and internal failures are written to the **log channel**; the caller gets an ephemeral line telling them to look there. The channel they were typing in is gone by then.
- A league correcting a classification therefore cannot iterate on a rejected block the way the original submission wizard allows — that one re-prompts in place. The asymmetry is not stated anywhere.

**The submission channel pings the one role that cannot see it.**
- `create_submission_channel` (`src/services/result_submission_service.py:85-96`) denies `@everyone` and grants the bot plus `server_configs.interaction_role_id`. The opening message at `:2301` mentions `divisions.mention_role_id` — the division's driver role — which holds no overwrite on the channel.
- Discord does not notify a member of a mention in a channel they cannot read, so the ping reaches nobody, and the league managers who *can* act on it are never mentioned. A league finds out a round is waiting by noticing the channel, or not at all.
- The spec says the bot shall notify "the trusted user role"; the code notifies the division role. `run_result_submission_job` never reads the interaction role for the mention, only for the overwrite.

**A points configuration cannot be read back between seasons.**
- `config_view` in `src/cogs/results_cog.py:645` fetches `get_season_for_server` and refuses with "No active or setup season found" when there is none, before it reaches the SETUP branch that reads the **server-level** store.
- The store is server-scoped and survives every season, but the only command that displays it demands a season exist. A league planning next year's points before running `/season setup` cannot see what it built.
- Same shape as the weather entry above: a server-scoped setting reachable only through a season-scoped surface.

**Nothing lists the configurations a server holds.**
- `/results config view` requires a name. `/season review` prints only the names *attached* to the current season (`src/cogs/season_cog.py:776`). No command enumerates `points_config_store`.
- A league that has forgotten whether it called the table `100%`, `Full` or `Standard` has no way to find out, and `/results config append` will happily accept the wrong guess — see the first entry.

**A zero-second penalty is accepted and produces a public verdict for nothing.**
- `_TIME_PENALTY_RE` in `src/services/penalty_service.py:17` is `^([+-]?\d+)s?$`, so `0` parses to a `TIME` penalty of zero seconds. `validate_penalty_input` has no zero check, and neither does `AddPenaltyModal.on_submit`.
- The penalty is staged, applied, recorded, and announced in the verdicts channel as a sanction, while changing no time, no position and no points.
- The README documented this input as rejected until 2026-08-17.

**The test-mode guard against a results job double-firing cancels a job ID that no longer exists.**
- `src/cogs/test_mode_cog.py:253` calls `cancel_job(f"results_r{entry['round_id']}")`, commented as handling "a real future-dated results_r job … so it doesn't double-fire". Results jobs have been named `results_s{S}_d{D}_r{R}` since the human-readable job-ID convention landed (`src/services/scheduler_service.py:406`), and `cancel_job` swallows a miss silently. The correct id is already on the entry as `job_id` and is not used.
- Suspicion rather than a confirmed fault: results jobs are excluded from `get_pending_advance_jobs`, and `run_result_submission_job` guards on an open submission channel, so the paths that would expose it are narrow — a weather-enabled test season with future-dated rounds, where `advance` opens the wizard and the genuine job then fires as well.
- Two docstrings in the same path — `get_next_pending_phase` (`src/services/test_mode_service.py:88-91`) and `get_pending_advance_jobs` — additionally claim `schedule_round` skips the results job for MYSTERY rounds. It does not; it schedules one for every format. `docs/how-to/test-mode.md` repeated the claim until 2026-08-17.

**A round whose every session is cancelled is scored, published and reviewed not at all.**
- `run_result_submission_job` (`src/services/result_submission_service.py:2494`) compares the cancelled set against the full session list and, on a match, closes the submission channel without calling `enter_penalty_state`.
- No standings snapshot is computed for that round, so the standings channel keeps the previous round's tables and the results channel holds only the per-session "this session was cancelled" notes. Attendance charges nobody, because the pipeline hangs off the penalty stage.
- Consistent and probably intended, but a round can therefore exist in a division's calendar with no standings row of its own, which anything walking rounds in order should expect.

## Images

Found on 2026-08-18 while building the `/images test` previews. **All three were fixed the same day**, and are kept here as a record of what was wrong and of the coverage gap that let two of them ship.

**Fixed — four image types joined a `signup_records` column that does not exist, and could not render at all.**
- `signup_records` has no `driver_profile_id` column in any migration — it is keyed by `(server_id, discord_user_id)`, as `PRAGMA table_info` on a freshly migrated database confirms, and as `placement_service.py:289` joined it correctly all along.
- Three sites joined it on the phantom column: `image_lineup_post.py`, and two in `image_results_post.py`. The second of those is `_nationalities`, which `attendance_service.py` and `image_verdict_post.py` both import, so the fault reached the lineup, the results, the attendance sheet and the verdict alike.
- The query raised `sqlite3.OperationalError: no such column: sr.driver_profile_id` before any drawing was assembled, so every one of those four aspects fell back to its textual posting whatever a league configured.
- **Why it shipped**: no test covered `build_drawing` for any of them. Every image suite exercises `resolve_drawing`, which is handed its rows and never issues a query. `tests/unit/test_image_post_signup_join.py` now runs the queries themselves against a migrated database.

**Fixed — a fifth site kept the phantom `signup_records` join, so no verdict ever drew a driver flag.**
- The fix above corrected three sites. `_driver_nationality` in `image_verdict_post.py` was a fourth, and was missed: it joined `signup_records sr` to `driver_profiles dp` `ON dp.id = sr.driver_profile_id`, the same column that has never existed.
- Its bare `except Exception` returned `None`, which reads as "this driver stated no nationality" rather than as a fault, so the verdict graphic drew and simply never carried a flag. Nothing in the reply or the log distinguished that from a league whose drivers had genuinely given none.
- **Why it outlived the others**: the earlier fix was driven by the renders that *failed*, and this one did not fail. It was found on 2026-08-24 while giving test-mode drivers a nationality, by reading every site that reads one.
- **Fixed** by joining on `(server_id, discord_user_id)` as the other four sites do; `server_id` is now threaded in from `build_drawing`, which already held it. Covered in `tests/unit/test_image_post_signup_join.py`.

**Fixed — the nationality suppression switch reached no graphic at all.**
- `_nationality_collected` in `image_results_post.py`, and a second inline copy in `image_lineup_post.py`, read `SELECT nationality_required FROM signup_config`. No migration creates `signup_config`; the setting lives in `signup_module_settings`.
- Both were wrapped in a bare `except Exception` returning `True`, so the missing table was swallowed on every render and the switch was never observed. A league that switched nationality collection off still got flags drawn on all four graphics that draw them.

**Fixed — the standings round-column grid was never implemented, so no standings graphic could render.**
- `StandingsDrawing` carried a `rounds: list[RoundHeading]` field and nothing ever assigned it: `resolve_drawing` took no `rounds` argument, `build_fill_spec` projected no round column, and the driver-cell and constructor-car resolution the wip-spec already specified had never been written.
- The packaged `standings_drivers_template.svg` declares a round column per round of a season. With nothing filling them, a render was abandoned with `no value could be determined for round_1_number, round_2_number, …`; the constructors template failed earlier still, on a separate capacity bug below.
- The classification itself was always sound — positions, points, gaps and movement all resolved — so the fault was confined to the grid of per-round columns beside it.
- **Fixed** by extending `resolve_drawing` and `build_fill_spec` in `image_standings_service.py` to resolve and project the grid — round headings, a session cell per driver's row, and per-constructor car allocation — exactly as `docs/wip-specs/image_module_specification.md` § "Standings image generation" already specified. The cross-session team invariant that section's car-allocation rule depends on ("a driver is never placed on two cars, nor on the cars of two teams") is now genuinely enforced, by a new check in `result_submission_service.validate_submission_block`; before this fix it was an unenforced assumption.
- **Why it shipped**: found on 2026-08-18 while building `/images test standings`, the first code ever to take the standings through a full render — the aspect had no posting path, so nothing else had exercised it.

**Fixed — the constructors preview counted the reserve team as an extra constructor.**
- `_team_role_ids` in `image_preview_service.py` built its role map from every team of the division, reserve included, and `build_standings_preview` used it unfiltered to build the constructors classification. A division of eleven real teams therefore drew twelve constructor rows against a template sized for eleven, and failed with `CAPACITY_EXCEEDED`.
- The drivers side of the same preview already filtered the reserve out via `_racing_drivers`; the constructors side had no equivalent.
- **Fixed** by a matching `_racing_teams` helper, used to build the constructors classification.
- **Why it shipped**: no test exercised the constructors preview against a division carrying a reserve team.

**Fixed — a rejected asset directory was reported as an unconfigured asset class.**
- Every posting path resolved its directories inside `try: … except Exception: pass`, so a configured path that was rejected was omitted from the map and the reason discarded. `utils/svg_fill.py` then reported `image field X names asset class Y, which is not configured` and abandoned the render — telling a league it had never set a directory it had in fact set.
- `resolve_configured_directories` in `image_render_service.py` now logs the reason and carries it onto the `FillSpec`, and the message names what was actually wrong. The original wording survives for a class genuinely never configured.
- **Reachability was always low**, and the entry should not be read as though it were not: `/images config *-directory` validates at the point of configuration — it rejects a path escaping the project root, rejects an empty value, and stores the result relative to the root. Nothing else writes those columns. Reaching the render-time rejection therefore takes a directory symlink or junction that moves outside the project *after* being set, or a hand-edited database.
- A directory that merely does not exist is a separate case and was never affected: it resolves, and its assets fall back as Rule XIV.13 requires.

**Fixed — a mandatory field inside a removed group was still demanded, so no lineup rendered unless the division fielded a reserve driver.**
- `_verify_against_data` in `src/services/image_render_service.py` did `checkable -= set(spec.remove)`. That subtracts the ids of removed nodes, but a group's **children** keep their own ids and stayed in `checkable`. The lineup removes `reserve_group` whenever the division fields no reserve driver (`image_lineup_service.py:341`), and `reserve_driver_1_name` — mandatory, and wrapped inside that group — was then reported as a value that could not be determined. The render was abandoned.
- It happened whether the division's reserve team instance carried empty seats or none at all, so it was the common case and not an edge: `seed_division_teams` creates no seats for a reserve team, and most divisions field no reserve. It reached the **posting path**, not only the preview.
- The `fill` step was correct throughout and returned no problem; only the verification standing in front of it was wrong. Constitution XIV.2 is explicit that a removed group takes its fields with it and that this "is not a failure", so the code contradicted the rule it cites.
- **Fixed** by `_removed_field_ids`, which walks each removed node's subtree and gathers the names it takes off the canvas — every `@id`, and an `inkscape:label` only where the node is a layer, matching how `FieldIndex` addresses a field.
- **Why it shipped**: no test rendered the shipped lineup template against a matching team list. The lineup suites use templates of their own whose reserve block differs, and the preview suites stop at assembling the fill spec, one step before this check. `tests/unit/test_image_render_removed_groups.py` now covers it against the shipped template. Found on 2026-08-19 while eye-checking a PNG for feature 046.

**A cancelled division is still offered as previewable.**
- `SeasonService.get_divisions` (`src/services/season_service.py:766`) selects every division of a season without filtering on `status`, so a division cancelled by `/division cancel` comes back with the rest. The `/images test` division autocomplete and the name resolution in `image_preview_service.resolve_context` both read that list, so a manager can complete on, and draw a preview for, a division the league has cancelled.
- The picture drawn is accurate for the data the division still holds, so nothing renders wrongly — the fault is that a preview offers a division the league has withdrawn. Other readers of `get_divisions` filter for themselves (`season_service.py:520` keeps `status != 'CANCELLED'`), which is what the preview path does not do. Found on 2026-08-19 while planning feature 046.

## Core setup and access

Found on 2026-08-17 while writing the core configuration how-to guide.

**The bot answers out-of-channel commands, which the constitution forbids.**
- Principle I states that the bot "MUST reject out-of-channel commands **silently** (no response)". `channel_guard` in `src/utils/channel_guard.py` logs a warning and replies "⛔ This command can only be used in the configured interaction channel." Its own module docstring and its inline comment both claim the silent behaviour it does not implement.
- A member who types a command in the wrong channel is told the bot exists and where its command channel is, rather than being ignored — which is the disclosure the principle was written to prevent.
- Either the code or Principle I is wrong. Which is a decision, not an oversight to correct silently, and the constitution may only be amended via `/speckit-constitution`. Note that `docs/how-to/configuring-the-core-bot.md` documents the observed behaviour, so a fix in either direction obliges a change there.

**`/bot-init` promises default teams it does not create.**
- `src/cogs/init_cog.py` calls `seed_default_teams_if_empty` under the comment "Seed default F1 teams + Reserve". The method inserts the Reserve team and nothing else.
- The behaviour is defensible — a league names its own teams — but a reader of the cog is told a full grid ships, and does not.

## Test mode

Found on 2026-08-18 while auditing the how-to guides against the implementation.

**`/test-mode review` reports every pending phase as having no job, because it probes job IDs the scheduler never creates.**
- `build_review_summary` in `src/services/test_mode_service.py:528` builds probe IDs of the form `phase1_r{round_id}`, `mystery_r{round_id}`, `results_r{round_id}` and `rsvp_notice_r{round_id}`, and tests them against the live set from `get_job_ids_for_rounds`. The scheduler creates `weather_p1_s{S}_d{D}_r{RoundNumber}` and its siblings (`src/services/scheduler_service.py:372-415`) — a different prefix, and the round *number* rather than the round *id*.
- No probe can ever match. `_phase_status` therefore returns ⚠️ "pending, no job" for every outstanding phase, and the ⏳ state it defines is unreachable. A maintainer reading the summary is told the schedule is empty when it is fully armed.
- `/test-mode advance` carries the same mismatch: its `cancel_job(f"results_r{round_id}")` at `src/cogs/test_mode_cog.py:253` names a job that does not exist, so the double-fire its comment claims to prevent is not prevented by it.

**`get_pending_advance_jobs` documents a phase number it cannot return.**
- Its docstring at `src/services/scheduler_service.py:609` lists `0=mystery notice`, but `_PHASE_PREFIX_MAP` at `:619` has no mystery prefix and a mystery round's notice is scheduled under `weather_p1`. Phase 0 arises only on the database path in `test_mode_service`.
- Harmless in effect — a mystery round comes back as phase 1 and `run_phase1` resolves the format and posts the notice — but the docstring is a third comment in this area describing behaviour the code does not have.

## Season lifecycle

**`get_setup_or_active_season` picks between a running season and a pending one arbitrarily.**
- `SeasonService.get_setup_or_active_season` (`src/services/season_service.py:78`) is `WHERE status IN ('SETUP', 'ACTIVE') ... LIMIT 1` with no `ORDER BY`. Where a league is building next season while this one runs, both rows match and which is returned is whatever SQLite happens to yield first.
- Its two callers are `/driver` commands (`src/cogs/driver_cog.py:123` and `:220`), so a driver operation can silently address the wrong season. Found on 2026-08-19 while planning feature 046, which needs the opposite guarantee and therefore adds a method of its own rather than reusing this one.

**`server_configs.previous_season_number` is written by nothing and read by nothing.**
- The column is added by `src/db/migrations/008_driver_profiles_teams.sql:71` and carried on the model at `src/models/server_config.py:15`. `SeasonService.increment_previous_season_number` (`src/services/season_service.py:502`) is the only code that would write it, and nothing calls that method.
- It therefore holds 0 on every server, whatever the league's history. The real previous season number is derived instead from `count_persisted_seasons`, which counts seasons that have reached ACTIVE, COMPLETED or CANCELLED status, and `save_pending_snapshot` numbers a new season at one higher than that tally.
- The trap is that the column is the obvious-looking source for anything wanting "the previous season's number" and is always wrong. Found on 2026-08-19 while specifying feature 046, whose fabricated league needs exactly that number.

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

**`ASSET_DIRECTORIES` is defined twice in `src/models/image_constants.py`.**
- The table appears in full at roughly line 161 and again at roughly line 236, with identical contents. The second definition wins at import; the first is dead.
- Found while repathing the eight defaults to `resources/defaults/` for feature 047, which had to edit both to keep them agreeing. They agree today, and nothing reads the first — but two tables one edit apart from disagreeing is a trap, and the next reader has no way to tell which is authoritative.
- `packaged_directory_for` added by 047 reads the winning table, so the packaged tier and the configured default cannot drift; the duplication is still worth removing.

## Behaviour worth knowing rather than fixing

These are deliberate, or at least consistent, but are surprising enough to be mistaken for defects.

- **Disabling test mode deletes every stored forecast message for the server**, not only those posted while test mode was active. `flush_pending_deletions` iterates all rows for the server. On a season already running this removes forecasts drivers were reading, and only the next phase restores anything.
- **Disabling test mode also deletes every fake driver on the server**, across all divisions, via `clear_all_test_drivers`. There is no confirmation and no per-division scope, so toggling the flag to check something destroys a roster that may have taken many `/test-mode roster add` calls to build.
- **Enabling test mode seeds points configurations onto the current season**, creating and attaching **Standard** and **Half Points** where none are attached. It is idempotent, but it means a season can acquire a points configuration as a side effect of a command that appears only to flip a flag.
- **A division created by `/division duplicate` does not inherit the source division's forecast channel.** Nothing warns at the time; it surfaces later as a season that refuses to approve.
- **Cancelling a round does not delete forecasts already posted for it.** The division is told no further forecast will follow, but the standing forecast remains.
- **Disabling the weather module clears nothing** beyond cancelling scheduled jobs — channels, deadlines, recorded phase results and posted messages all survive. This differs from the general rule stated for `/module disable`.
