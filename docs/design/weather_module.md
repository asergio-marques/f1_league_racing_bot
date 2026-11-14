# Weather — why the code is shaped as it is

This file says how the weather module's code is laid out and why: its tables and the columns it keeps
on core's, its services, its changes on the queue, its timed work and start-up, what it posts, how it
meets core and the image module, how it fails, and the constraints a later reader might tune away.
Every path is under `src/leaguebot/` unless it says otherwise.

It cites rules rather than restating them. What binds every module is `architecture.md`'s, cited by
its section heading; core's side of a seam is `core.md`'s; what a league sees is the weather
specification's (`docs/wip-specs/weather_module_specification.md`), cited by its section heading. A
constraint on one function lives in that function's docstring, pinned by its test, and is named here
only where the shape depends on it. Where this file and the code disagree about what the code does,
the code wins, and the file is corrected at close-out.

---

## The database

Answers to `architecture.md`, "The database".

### Weather's tables

Weather owns three tables (`TABLE_OWNER` in `tests/repository/test_architecture_rules.py`), all
declared in `core/db/migrations/001_baseline.sql`.

| Table | One row is | Held to it by |
|---|---|---|
| `phase_results` | one draw of one phase (1 to 3) of one round: the inputs and the outputs as a JSON `payload`, its `status` and when it was made | `CHECK` on the phase number and on the status; a partial unique index on `(round_id, phase_number)` where the status is `ACTIVE` |
| `forecast_messages` | one posted forecast of one phase (0 to 3) of one round of one division: the id of its last message, every id when it was sent in parts (`message_ids`), and the channel it was posted in (`channel_id`) | `UNIQUE (round_id, division_id, phase_number)`, `CHECK` on the phase number |
| `weather_pipeline_config` | the league's three horizons (`phase_1_days`, `phase_2_days`, `phase_3_hours`) | `CHECK (id = 1)`; every value `NOT NULL` with the packaged default as its column default |

A withdrawn draw is not deleted: its row is kept as `INVALIDATED` beside the standing one, which is
why the unique index is partial. The ordering of the three horizons (a phase 1 further out than
phase 2, and phase 2 further out than phase 3) is a rule between settings, so it is judged by the
service that writes them and not by the schema (`architecture.md`, "The database": a rule that
compares one setting with another).

**A phase's status is an enum, and the schema repeats it.** `PhaseResultStatus`
(`weather/models/forecast.py`) holds `ACTIVE` and `INVALIDATED`, the list is not expected to grow, so
the schema carries it as a `CHECK` and a test ties the two (`test_phase_result_statuses_match_the_schema`).
The standing draw of a phase being one is the schema's too, as a key, not the service's:
`test_a_second_standing_draw_for_one_phase_is_refused`.

### Weather's own columns on core's tables

Four of core's tables carry seven columns that are weather's alone (`OWN_COLUMNS`), and core reads none of
them: what each means for a round is weather's rule.

| Column | Meaning |
|---|---|
| `rounds.phase1_done`, `phase2_done`, `phase3_done` | a draw of the phase stands: it has been drawn and not withdrawn since |
| `rounds.mystery_notice_given` | a mystery round's notice has been recorded; the notice is no forecast, so it raises no phase flag |
| `sessions.phase2_slot_type` | the phase 2 draw for the session: `rain`, `mixed` or `sunny` |
| `divisions.forecast_channel_id` | the channel the division's forecasts are posted in |
| `server_configs.weather_module_enabled` | the module's switch |

Weather reads its own column on a core table through a function of its own, where the reading rules
name one (`READS_OF_OWN_COLUMNS`: `round_phases.phases_drawn_on` for the flags, `forecast_channel.forecast_channels_on`
for the channel, `round_phases.phase2_slot_types_on` for the slot type), and a round, a division, a
season, a track or a round's sessions through core's readers (`round_facts_on`, `division_on`,
`get_track_by_name`, `sessions_of_round_on`). No weather service writes a core table's column that is
not its own. The image module reads none of weather's tables: weather hands it the forecast's words,
composed, to draw the graphic (see "How modules and core fit together"). Phase 3's slots are held in
the phase 3 draw's `payload` alone, so no column carries them.

**One flag says a draw stands, and another says a notice was given.** A mystery round's notice is no
forecast (weather specification, "How a forecast names itself"), so it does not raise `phase1_done`:
what weather says is drawn, to the amendment's judgement, to the start-up recovery, to `/test-mode
advance` and to `/season status`, is read through `phases_drawn_on` alone, where `PhasesDrawn`
holds the three phases and the notice apart and `first_done` says which of them is a round's first
forecast. Were the notice to raise `phase1_done`, a mystery round changed to another type would be
refused for a forecast never drawn, and its Phase 1 would never fire. The notice's flag is lowered
as the notice's record is dropped (`forget_forecast_on`), so a round that is mystery again is owed
its notice again. Core carries none of the four flags.

### The switch

The module's switch is `server_configs.weather_module_enabled`, as `architecture.md`'s "Each module
turns itself on and off" has a module keep it. `draw_guard.weather_enabled_on` reads it on a
connection, and the reader weather registers with `ModuleService` is that function
(`wiring.register_switch`). A job's check and a draw read it themselves. Switching
off is a save of the flag and an audit entry, then an `After` step that removes the season's weather
timers (`SchedulerService.cancel_all_weather`): the flag is read by every job's check, so a job that
fires in between is dropped, which is why the removal can follow the flag
(`module_switch_settings`' docstring, `tests/weather/test_weather_module_gate.py`). The forecasts
already posted and the draws already made are kept by a switch-off.

### Who writes each table

| Table | Written by | What it writes |
|---|---|---|
| `phase_results` | `phase1_service`, `phase2_service`, `phase3_service` | a phase's draw, with its flag, in the draw's save |
| | `phase_withdrawal` | a withdrawn phase's rows set `INVALIDATED`, inside an amendment's save |
| | `forecast_cleanup_service` (`rounds_deleted_on`) | a deleted round's rows |
| `forecast_messages` | `forecast_cleanup_service` | a row as a post is recorded (`store_forecast_message_on`), dropped as its message is deleted (`forget_forecast_on`, which lowers `mystery_notice_given` with a notice's row), a deleted round's rows, and every row when the bot is packed (`bot_packed_on`) |
| `weather_pipeline_config` | `weather_config_service` (`set_deadline_on`) | one horizon, in a setting's save |
| `rounds.phaseN_done` | the draws | raised with the draw |
| | `phase_withdrawal` | lowered with the withdrawal |
| `rounds.mystery_notice_given` | `mystery_notice_service` | raised in the save that goes before the notice's post |
| `sessions.phase2_slot_type` | `phase2_service`; `phase_withdrawal` | written with the draw; cleared with the withdrawal |
| `divisions.forecast_channel_id` | `forecast_channel` (`set_division_forecast_channel`) | the channel, in the setting's save |
| `server_configs.weather_module_enabled` | `module_switch_settings` | the flag, naming only it |

Every writer works on the connection it is handed and commits nothing: a draw is handed the queue's
save, an amendment's withdrawal the amendment's, a setting's write the setting's.

### The stored copies

Weather keeps one thing twice that cannot be worked out again: **a draw's outputs.** Phase 2 and 3
are random draws, so the figures are not a function of any other row. The draw's `payload` in
`phase_results` is the record; `sessions.phase2_slot_type` holds phase 2's outputs where phase 3
reads them, and `rounds.phaseN_done` says the draw stands.
It is a stored copy that cannot be recomputed, in `architecture.md`'s "The database" sense; its
writers are these.

| Writer | Writes together | Pinned by |
|---|---|---|
| `draw_phase1_on` | the phase 1 result and `phase1_done` | `test_the_draw_writes_only_on_the_connection_it_is_handed`, in `tests/weather/test_phase1_draw.py` |
| `draw_phase2_on` | the phase 2 result, every session's slot type and `phase2_done` | `test_the_draw_writes_only_on_the_connection_it_is_handed`, in `tests/weather/test_phase2_draw.py` |
| `draw_phase3_on` | the phase 3 result and `phase3_done` | `test_the_draw_writes_only_on_the_connection_it_is_handed`, in `tests/weather/test_phase3_draw.py` |
| `withdraw_phases_on` | the rows invalidated, the sessions' slot types cleared and the flags lowered | `test_withdrawing_phases_writes_only_those_phases_on_the_connection_handed` |

The one case where the text a league reads is a copy, the forecast's words, is not stored at all: it
is composed from the stored payload when the forecast is sent ("Constraints a later reader might
tune away").

### What is deleted, and by which hook

Weather's tables link to `rounds` and `divisions` with no delete action, so every path that removes
one of those removes weather's rows first, which is what weather signs up to core's delete hooks for.

| Hook | Function | What it does |
|---|---|---|
| `rounds_deleted` | `forecast_cleanup_service.rounds_deleted_on` | deletes the rounds' forecast rows and draws; the flags and the session columns go with the rounds and sessions core deletes |
| `bot_packed` | `forecast_cleanup_service.bot_packed_on` | forgets every posted forecast, so nothing edits a message of a server the bot has left |
| `test_mode_flushed` | `forecast_changes.test_mode_flushed_on` | hands back the `flush_forecasts` job, which plans the deletion of every forecast test mode posted (see "The forecast change and its steps") |

The forecast channel is a column of `divisions`, so it goes with its division. Each hook works on the
delete's own connection and commits nothing (`architecture.md`, "Deletes are proven by a test, and
each module deletes its own rows").

### What a restart loses

Nothing the database holds. A draw, its flag, a posted forecast's ids and channel are rows. What a
restart leaves to be done is found from them by the start-up steps ("Timed work and restarts").

---

## How the code is laid out

Answers to `architecture.md`, "How the code is laid out". Weather's code sits in `weather/cogs/`,
`weather/services/`, `weather/models/` and `weather/utils/`, beside `weather/wiring.py`. Its services
are one flat folder of eighteen files, as `architecture.md` keeps a module's services unless its
design file groups them.

### Services

| Service | What it owns |
|---|---|
| `forecast_changes` | the `weather.forecast` and `weather.forecast.clean_up` changes, their jobs, the timer handlers and `/test-mode advance`'s steps, the jobs weather defines for a season's end and the test-mode flush, the steps it plans in an amendment, and the words for weather's jobs left undone |
| `forecast_amendment` | weather's part of an amendment: its judgement, and its answer when the round is armed again |
| `forecast_recovery` | the two start-up steps |
| `forecast_cleanup_service` | what is recorded of a posted forecast, the send of a forecast, and the plan to delete forecasts |
| `phase1_service`, `phase2_service`, `phase3_service` | the three draws |
| `mystery_notice_service` | the record of a mystery round's notice, which posts nothing |
| `draw_guard` | the one reader of a round as weather judges it, the guards a draw holds to, and the switch's reader |
| `round_phases` | weather's record of the phases a round has had drawn (`phases_drawn_on`), the slot types phase 2 drew, the phases due at a moment and whether a round's first horizon is still ahead |
| `phase_withdrawal` | the withdrawal of phases inside an amendment's save |
| `forecast_channel` | the forecast channel's writer and readers |
| `weather_config_service` | the three horizons, their reading, ordering and writing |
| `weather_settings`, `module_switch_settings` | the two settings and the switch, as `Setting`s |
| `cancellation_notice` | the note that no forecast is coming |
| `fingerprint_areas`, `test_mode_steps` | what weather hands the season's fingerprint, and `/test-mode advance` and its review |

Services take the database's path, a connection or the queue's clock as a parameter; none looks a
service up on the bot, and one that posts is handed the bot or reads it from its step's context.
The draws use `random` directly, so their tests patch it.

### The cog

`weather_cog` holds the `/weather` command group: `config phase-1-deadline`, `phase-2-deadline`,
`phase-3-deadline` and `view`, and `channel`. It holds no rule, and its commands take three
shapes. The three deadline setters ask their setting's change at once and leave the switch to the
change's check. `view` checks the switch, reads the horizons and replies, asking nothing of the queue.
`channel` checks the switch, finds the division the manager named and asks the setting's change.

### The wiring and the models

`wiring.py` is under the entry point's rules, not a service's (`architecture.md`, "How the code is
laid out"): it reads the bot as the builder does, signs weather up to each hook, registers its change
types, its timer handlers and the steps of `/test-mode advance`, and names its start-up steps. It
holds no rule of weather's and no database code of its own. The models are plain data:
`PhaseResultStatus` and `Drawn` (`forecast.py`), `WeatherPipelineConfig` and `RecoveryFailed`. The
utils are pure: `math_utils` draws, `message_builder` composes a forecast's words.

---

## How a change is carried out

Answers to `architecture.md`, "How a change is carried out".

### The forecast change and its steps

`weather.forecast` draws, posts and supersedes one phase's forecast, as three kinds of job of one
change, so that a stop between any two resumes where it stopped:

1. **`draw_forecast`**, a save: the phase is drawn and recorded, its flag set and its calculation
   line written to the log channel, in one save (weather specification, "Supersession and cleanup"),
   the line naming the round as that section's rule on naming it asks: through core's own naming
   (`round_called_on`, read on the save's connection, handed to `phase_log_message` as `called`).
   A phase whose earlier phase has not been drawn plans the earlier phase first. A Phase 1 that
   cannot be drawn, for a round naming no circuit, a circuit that cannot be resolved or an
   impossible distribution, writes nothing but its BLOCKED line, which reaches the log channel as
   the specification's "Phase 1" says. A mystery round's notice is this job too, with phase 0: it
   records `mystery_notice_given` and draws nothing.
2. **`post_forecast`**: the forecast is posted. Its record saves the message's ids and channel, and it
   plans the deletion of the forecast it supersedes. The picture, where one is due, is handed the
   forecast's words, composed here (`forecast_values`), and any reason one was due but not drawn
   comes back as a line of this job, written to the log channel (see "Posting to Discord").
3. **`delete_forecast`**: the deletion of one superseded or cleaned-up forecast, the record dropping
   the row. A forecast posted in parts plans a `delete_forecast_part` job for each earlier part.
   That job is the shared standing-post delete under weather's own name and words
   (`DELETE_FORECAST_PART`, from `standing_posts.delete_replaced_step`), so that core's shared step
   names no forecast: a part a league admin discards is worded by weather, whether it was found at a
   season's end or in an amendment.

**The test-mode flush is weather's job.** `flush_forecasts` plans a `delete_forecast` for each forecast
test mode posted, so that a refusal of one stops the queue there. Core names it nowhere: a change
that switches test mode off asks the `test_mode_flushed` hook for the jobs to plan, and weather's
`test_mode_flushed_on` hands this one back. The abort plans it before the save that switches test
mode off, so core hands the hook a flag, which weather puts in the job's payload, and the job reads
as it comes up that test mode is still on, clearing nothing where it is not. Weather defines the
step for a season's end (`season_end_steps`), with its own words for it left undone.

A phase draws once: the change is not repeatable, its key is `weather.forecast:<round>:<phase>`, and
its check drops a second ask as not due once the draw stands. The check, the draw's own guard
and the post's each judge whether the work is still due through one function (`_not_due`, over
`draw_guard.lapse` with the round's format and race time): the module on, the round there and not
cancelled, its division not cancelled, its format still the one the forecast is for, its race not
yet run. A forecast late for its race is dropped as the change starts, and a draw stopped before
its race and tried after it is dropped as it comes up, drawing, recording and logging nothing; a
post is dropped likewise, a draw already made being kept (weather specification, "Supersession and
cleanup"). Every moment is judged at the queue's clock.

### The clean-up change

`weather.forecast.clean_up` plans a `delete_forecast` job for each of a round's phase 1 to 3
forecasts that stands, a day after the race by `clean_up_moment`. A round called off, or in a division
called off, is owed none; a round that ended cancelled only because every session was entered as not
held is owed it as any is; a mystery round's notice is never cleaned up (weather specification,
"Supersession and cleanup" and "Changes to a round after generation").

### The settings and the switch

The deadline setting (one setting for the three horizons), the forecast channel and the switch's two directions are `Setting`s built
by `setting_change`, which holds what every settings change does with a value already held and with a
fault. Their checks run as the command is answered and again as the change starts. The forecast
channel's setting is handed core's `channel_uses_read` hook, as results' and attendance's are, so
that it asks which job a channel already does without importing the module that holds the answer.

### The steps weather plans for an amendment

`forecast_changes.amendment_steps` defines the steps an amendment plans through weather: the
deletion of a withdrawn forecast (`delete_forecast`), the notice that the forecasts no longer stand
(`notify_invalidation`), the draw of a phase now due (`rerun_phase`) and the clean-up a day after the
round (`clean_up_forecast`). The step names, their payloads and the changes weather stores are the
queue's record: they do not move for a design, so that a change stopped before a deploy retries
after it.

**A mystery round's notice is no forecast, in an amendment either** (weather specification, "Mystery
rounds" and "Changes to a round after generation"). The notice's taking down is a `delete_forecast`
job of phase 0, which `forecast_amendment.round_armed` plans first. The deletion goes through the
standing-post handler, so Discord refusing it stops the queue for Retry or Discard, and the record it
closes is the notice's, `mystery_notice_given` being lowered with its row. What the notice is judged
as, and what becomes of it, is the specification's.

### The cancellation notice

A round, division or season called off posts weather's note through `cancellation_notice`, a job of
its own on the queue, sent silently to the forecast channel. A channel never set is a line saying so;
a channel set and gone, or a send Discord refuses, stops the queue for Retry.

### What must be all or nothing

A draw with its calculation line, so that a line in the log channel always has a draw behind it and
the reverse; a draw's flag, its payload and its session columns ("The stored copies"); a posted
forecast's row with the job's mark. Each is one save on the connection the step is handed, and a
test pins each.

---

## Timed work and restarts

Answers to `architecture.md`, "Timed work and restarts".

### The kinds, and their arming

Weather has four kinds of timer: `weather_p1`, `weather_p2`, `weather_p3` and `cleanup`. A mystery
round's `weather_p1` is its notice and its two others do nothing. Core's
scheduler service arms them (`schedule_round`, and `schedule_round_cleanups` for the clean-up alone)
from the horizons weather hands core and the moments `round_event_moments` places. **All four are
armed with no lateness limit** (`misfire_grace_time=None`), so a timer missed while the bot was down
fires once it is back, as the core specification's "When the bot stops" requires, and the work's own
check decides whether it is still due. Pinned by `test_a_phase_timer_missed_while_down_and_asked_by_the_restart_draws_once`
and `test_a_past_dated_forecast_clean_up_armed_again_takes_down_once`.

A timer, the start-up sweep and `/test-mode advance` reach the same asks (`ask_forecast`,
`ask_forecast_clean_up`) and the same keys, so a phase asked by both is drawn once and a forecast
asked by both is taken down once.

### The two start-up steps

Weather's turn of the start-up sweep is two steps, named in `wiring.start_up_steps`, each a function of
`forecast_recovery` handed the database's path and the change queue, in the order they run:

1. **missed phases** (`recover_missed_phases`): each phase whose horizon passed while the bot was
   down, for a round whose race is still ahead, is asked; a mystery round's notice included (weather
   specification, "Recovery"; core specification, "When the bot stops");
2. **missed forecast clean-ups** (`recover_missed_forecast_clean_ups`): each forecast whose clean-up
   fell due is asked to come down.

The phases come before the clean-ups. A recovery posts nothing: it asks the queue, which runs what is
asked once it starts, and every moment it judges is read at the queue's clock (`ChangeQueue.now`). The
sweep passes over a module that is off, so neither step reads the switch.

The horizons are the league's own, placed by `round_event_moments` with the module's `WeatherWindows`,
as the scheduler places them. `phases_due` is not used: it drops a mystery round, and the restart must
still post a mystery round's notice. A round is read through core's reader of the active season's
rounds, and the clean-ups from weather's own `forecast_messages` rows, each round asked of core through
`round_facts_on`.

**A round that fails stops none of the others.** Once every round has been gone through, the failures
are raised as one `RecoveryFailed` naming each round and its error, which the start-up sweep reports to
the log channel in one line, as the core specification's "When the bot stops" requires. Each module
owns its own `RecoveryFailed`: core's sweep catches whatever a step raises and does not name the type.
A start time that cannot be read is that round's failure, found by each step in its own way: the
missed phases from the ids `rounds_of_the_active_season` hands back apart from the live rounds, and
the clean-ups from `round_facts_on` raising for it. Each step records it. A database that cannot be read
is raised as it is. Pinned by `test_one_round_failing_its_recovery_stops_none_of_the_others`,
`test_a_round_whose_start_cannot_be_read_fails_its_missed_phases_alone` and
`test_a_round_whose_start_cannot_be_read_fails_its_recovery_alone`.

### One shared delay for the clean-up

The clean-up of a round falls a day after its race, a delay `clean_up_moment` holds once
(`POST_RACE_CLEANUP_DELAY`), for the forecast's clean-up and attendance's check-in clean-up alike, and
for the scheduler's timer, the restart and the amendment's arming of each. Pinned by
`test_schedule_round_cleanups_arms_both_clean_ups_from_the_post_race_delay`, and for the forecast's
restart step by `test_the_forecast_clean_up_catch_up_reads_the_queue_s_clock`.

---

## Posting to Discord

Answers to `architecture.md`, "Posting to Discord".

A forecast is a standing post: posted through the standing-post handler (`post_standing`), superseded
by the next phase's, and deleted through the same handler (`delete_standing`) from the channel and
message ids recorded when it was posted, whether it went as text or as a graphic. The mystery notice
is a standing post of the same kind. The note of a cancellation is a notice, sent silently.

A forecast is found again through `forecast_messages.message_ids` and `channel_id`: a forecast posted
in parts deletes every part, and one posted before the channel was recorded is looked for in the
division's present channel.

**The graphic falls back to text, and says why.** The image module draws the forecast's graphic, on
the first try alone, from the words weather hands it (`ForecastValues`, declared by image) and
reading none of weather's tables. Where `attach_forecast` returns no file, the forecast goes as
text. It hands back, with the file or without, the lines saying why a picture that was due was not
drawn, which reasons and when none is due being the specification's "Image generation". The two
switches are asked first, so that nothing is read for a picture not due. The graphic holds two catch-alls
(`architecture.md`, "Errors and failures"): one around its drawing, the place for a graphic that
rides on a post, and one around reading the round to name the line of a switch that could not be
read, the place for naming something in a line, which leaves the line naming the phase alone. The post job puts the lines on its `StepResult`, so they reach the
log channel in the save that records the post. The render is given no bot, so a render's notices
come back as those lines alone and are posted once. A graphic Discord refuses stops the queue for
Retry.

---

## How modules and core fit together

Answers to `architecture.md`, "How modules and core fit together".

### The hooks weather signs up to

Weather declares no hook of its own. Core declares them and weather subscribes in `wiring.py`; where
several modules join a hook the entry point signs them up in the order they are called.

| Hook | Weather's function | What weather does |
|---|---|---|
| `starting` | `wiring.start_up_steps` | its two start-up steps |
| `round_windows_read` | `weather_config_service.round_window` | hands its three horizons, or none where the module is off and the round is not being amended; the season's approval and review read them, and an amendment does not |
| `phases_drawn_read` | `round_phases.phases_drawn` | says which phases of a round are drawn; `/season status` reads it |
| `amendment_judged` | `forecast_amendment.amendment_judged` | weather's part of judging a round's amendment: the two refusals for a forecast already drawn, its note, the phases it withdraws and its own horizons, all kept in the part |
| `round_amended` | `phase_withdrawal.round_amended_on` | withdraws the phases, inside the amendment's save |
| `round_armed` | `forecast_amendment.round_armed` | answers what the round's forecasts are armed with (`ForecastJobs`), and the steps owed |
| `round_clean_ups_due` | `forecast_changes.amendment_clean_ups` | the clean-ups due |
| `amendment_steps`, `amendment_step_failure` | `forecast_changes.amendment_steps`, `forecast_failure` | the steps an amendment asks of weather, and its words for one left undone; the entry point signs the second up, weather then attendance |
| `cancellation_notices`, `cancellation_steps` | `cancellation_notice` | its note |
| `season_end_steps` | `forecast_changes.season_end_steps` | the deletion of forecasts left standing at the season's end, and `flush_forecasts`, each with its words |
| `test_mode_flushed` | `forecast_changes.test_mode_flushed_on` | hands back the `flush_forecasts` job |
| `rounds_deleted`, `bot_packed` | `forecast_cleanup_service` | see "The database" |
| `division_lines_read`, `division_channel_faults_read`, `channel_uses_read`, `setting_channels_read` | `forecast_channel` | its channel, read for the reviews, the channel registry and the factory reset |
| `review_sections_read` | `weather_config_service.review_section` | its section of a season review |
| `fingerprint_areas_read` | `fingerprint_areas.fingerprint_areas` | its channels and horizons in the season's fingerprint, whatever its switch |
| `pending_steps_read`, `review_marks_read` | `test_mode_steps` | a round's forecasts for `/test-mode advance` and its review |

Weather's switch is the keyed hook `ModuleService.register_switch` under `"weather"`.

### An amendment's judgement and arming

Core judges what is its own: unknown fields, an empty change, the round's status, a move into the
past, a round already started, and a mystery round given a circuit alone. It then asks each module for its part through
`amendment_judged`, attendance's and then weather's, handing the round, the changes, the new moment,
one UTC `now` and the save's connection or none. **Core hands no module's windows, and keeps none by
name:** each module reads its own horizons (on the save's connection in a save, and through its own
reader outside one) and keeps them in its part. Weather answers an `AmendmentPart` (the refusals, its
note, `withdrawn`, and `weather_windows`, which core keeps for the amendment's save and the arming).
Core merges the parts, giving the refusals in core's head rules, each module's in sign-up order,
then core's tail rules. A key two modules keep is a fault.

What the judgement keeps is the queue's record: the `judge` job's result is `{judged, kept}`, `kept`
being the parts' merged `kept`, which core stores as it is given and reads for no module. The save
and the arming hand it back, and weather reads its horizons from it (`windows_kept`), **reading them
afresh where it finds none**: an amendment stopped across an update holds a record with no window of
weather's own, and nothing raises. The note names only the phases drawn and put back in question, and
promises a redraw only while weather is on, as "Changes to a round after generation" has it.

When the round is armed again, each module answers `round_armed` with a `RoundArming`, its steps and
its jobs; weather's `ForecastJobs` carry the windows and `skip_phases`, the phases whose draw the
amendment makes at once and whose timer therefore must not draw it again, since a timer with no
lateness limit would. Core makes the scheduler's calls, in a fixed order, from the answers;
it holds no rule of weather's about which jobs a round owes. A round already raced, or ended not held,
is armed its clean-ups alone by `schedule_round_cleanups`, which core calls from the modules'
switches, the clean-up's delay being the one shared delay, so no window is read. What the queue
stores of an amendment (the judgement's `kept`, the steps and their payloads) is core's record.

### What weather reads of others

Core's readers, as `architecture.md`'s "Each table is read and written by its owner alone" asks: a
round's facts and its division (`round_facts_on`, `division_on`), a round's sessions in id order
(`sessions_of_round_on`), a track by name (`get_track_by_name`), the rounds of the active season
(`rounds_of_the_active_season`). The composed read of a round as weather judges it is
`draw_guard.weather_round_on`, core's half beside weather's own.

### The dependency table and the image module

Weather needs no other module (no entry in `NEEDS`). It may ask the image module to draw a forecast's
graphic (`image_weather_post.attach_forecast`), and uses no other module. The image module reads none
of weather's tables: weather hands it the words, composed as the text composes them
(`forecast_changes.forecast_values`), in a model image declares (`ForecastValues`: the phase's
description, the likelihood of rain as text, and each session's label, weather type and slots in
the order they are run), so that image imports no other module and holds no second copy of weather's
wording. For `/images test`, image composes sample words of its own.

### The forecast provider

There is none outside the bot. A phase's inputs are the track's mean and spread of the chance of rain
and the previous phase's draw, and `math_utils` draws a Beta variate, a slot pool and a weighted
choice from them. A draw made is stored, not recomputed, which is why it is a copy ("The stored
copies").

### The phase model

Phase 0 is a mystery round's notice, which names no circuit and draws nothing. Phases 1 to 3 are, in
order: the chance of rain for the round, the rain, mixed or sunny assignment to each session, and each
session's slot-by-slot weather. A phase's input is the one before it, so a phase whose earlier phase
is not drawn plans the earlier phase first. Each phase's horizon is the league's own and no fixed
moment (weather specification, "Generation of weather").

---

## Errors and failures

Answers to `architecture.md`, "Errors and failures".

| Starting point | Weather's part |
|---|---|
| A command | the cog refuses with the base classes' words |
| A change | the check refuses a manager's request and drops the bot's where the work is no longer due; a job Discord refuses raises `StepFailedOnDiscord`, which stops the lane with the stop notice, Retry and Discard; a fault of the bot's own is raised as it is |
| A timed job | the timer's handler asks the change; a handler whose ask raises reaches the host's log |
| A start-up step | `RecoveryFailed`, above |
| A graphic | the fallback above: the forecast goes as text, and the reason reaches the log channel |

**What a Discard leaves** is worded by weather's `forecast_failure` for each of its steps, handed to
core's `amendment_step_failure` so that core names none of them. A discarded draw is made when the bot
next starts, while its round is still to be run; a discarded post leaves the draw standing in the log
channel; a discarded deletion is left to delete by hand, by its link, a mystery round's notice
being named as the specification's "Mystery rounds" has it.

---

## Constraints a later reader might tune away

Each is stated where it lives and pinned by the test named.

| Constraint | Stated in | Pinned by |
|---|---|---|
| A forecast's text is composed when it is sent, from the stored draw, not stored | `forecast_text`'s docstring | `test_the_forecast_text_is_built_from_the_recorded_draw` |
| A forecast is deleted from the channel it was posted in, not the division's present one | `forecast_cleanup_service`' module docstring | `test_a_forecast_is_deleted_from_the_channel_it_was_posted_in_after_the_channel_moves` |
| The deletion of a superseded forecast is a job of its own, after the post | `forecast_changes`' docstring | `test_a_phase_posts_before_the_superseded_forecast_is_deleted_by_a_job` |
| A mystery round writes no `phase_results` and tags nobody | `mystery_notice_service`'s docstring | `test_the_mystery_notice_notifies_nobody` |
| A mystery round's notice is recorded in a flag of its own, never in `phase1_done` | `round_phases`' module docstring | `test_the_notice_s_record_writes_only_on_the_connection_it_is_handed` |
| A mystery round changed to another type has its notice taken down by the amendment, and one whose date is moved keeps it | `forecast_amendment.round_armed`'s docstring | `test_a_mystery_round_changed_to_another_type_after_its_notice_takes_the_notice_down`, `test_a_mystery_round_whose_date_is_moved_keeps_its_notice` |
| A phase has one standing draw | the schema's partial unique index | `test_a_second_standing_draw_for_one_phase_is_refused` |
| A draw and its calculation line are one save | `forecast_changes`' module docstring | `test_a_phase_s_draw_and_its_calculation_line_are_saved_together_before_its_forecast_is_posted` |
| The horizons are the league's own, and no fixed moment is named | `phase1_service`'s docstring | `test_the_league_s_own_horizons_are_used_not_the_packaged_ones` |
| The four timers have no lateness limit; the work's own check judges lateness | architecture.md's "Timed work and restarts", and `SchedulerService.schedule_round`'s docstring (under `skip_phases`) | `test_a_phase_timer_missed_while_down_and_asked_by_the_restart_draws_once` |
| A recovery judges every moment at the queue's clock | `forecast_recovery`'s docstring | `test_the_missed_phases_catch_up_reads_the_queue_s_clock` |
| A failed recovery stops none of the other rounds and is reported once | `forecast_recovery`'s docstring | `test_one_round_failing_its_recovery_stops_none_of_the_others` |
| A phase the amendment draws at once is skipped by its timer | `ForecastJobs`' docstring | `test_core_arms_what_a_module_s_arming_answer_hands_back` |
| A mystery round's restart still posts its notice | `recover_missed_phases`' docstring | `test_a_missed_mystery_notice_is_asked_for_when_the_bot_starts` |
| The switch-off removes timers after the flag is saved | `module_switch_settings`' docstring | `test_phase_runner_produces_nothing_while_weather_is_disabled` |
