# Signup — why the code is shaped as it is

This file says how the signup module's code is laid out and why: its tables, what a signup belongs
to and the states its wizard passes through, its services, cogs and command group, its changes on
the queue and the two lanes they run on, its timers and start-up, what it posts, how it meets core,
how it fails, and the constraints a later reader might tune away. Every path is under
`src/leaguebot/` unless it says otherwise.

It cites rules rather than restating them. What binds every module is `architecture.md`'s, cited by
its section heading; core's side of a seam is `core.md`'s; what a league sees is the signup
specification's (`docs/wip-specs/signup_module_specification.md`), cited by its section heading,
and the core specification's where a rule is core's. A constraint on one function lives in that
function's docstring, pinned by its test, and is named here only where the shape depends on it.
Where this file and the code disagree about what the code does, the code wins, and the file is
corrected at close-out.

---

## The database

Answers to `architecture.md`, "The database".

### Signup's tables

Signup owns seven tables (`TABLE_OWNER` in `tests/repository/test_architecture_rules.py`), all
declared in `core/db/migrations/001_baseline.sql`. Every one of them is written, as the bot runs, on
a connection the change queue or a core hook hands in.

| Table | One row is | Held to it by |
|---|---|---|
| `signup_module_config` | the module's configuration: the signup channel, whether the window is open, the Sign Up button's message, the open window's tracks and close time, and the closed notice's message | `CHECK (id = 1)` |
| `signup_module_settings` | the three question settings (nationality, time type, time image), and where the last "signups are closed" notice stands (`closed_notice_channel_id`, `closed_notice_message_id`) | `CHECK (id = 1)`; the settings are `NOT NULL` with column defaults |
| `signup_availability_slots` | one time slot a driver may give as available, by day and time | `UNIQUE (day_of_week, time_hhmm)` |
| `signup_windows` | one opening of the window for a season: its tracks, its close time, when it opened and when it closed | `idx_signup_windows_one_open`, a partial unique index on the season where `closed_at IS NULL`: a season has one open window at a time |
| `signup_wizard_records` | one account's wizard: its state, its signup channel, the configuration it started against (`config_snapshot_json`), its draft answers, its last activity, when correction choices were offered (`correction_offered_at`) and when the hold on its channel runs out (`held_until`) | `discord_user_id UNIQUE` |
| `signup_records` | one completed signup: its season, the window it came through, the account it was made from, its answers, its lap sum (`total_lap_ms`) and whether it is approved | keyed by its id; indexed by account and by season |
| `season_signup_config` | the signup configuration a season was confirmed with: the three settings and its slots (`slots_json`), stamped `captured_at` | keyed by the season |

**The configuration row and the settings row are apart** because the switch-off forgets one and
keeps the other ("What disabling forgets and keeps", below). The closed notice's place is kept in
both: in the configuration while the module is on, and in the settings so that it outlives a
switch-off (`closed_notice_of`'s docstring; the schema's comment on the two columns).

**One open window to a season is the schema's rule**, not a service's: a close time and a close are
written to "the window whose `closed_at` is null", which is only one row because the index refuses a
second (the index's comment; `test_a_second_open_window_is_refused`). The switch-off stamps the open
window closed in its save for the same reason, or the next opening would make a second one
(`signup_off_change`'s `switch_off`).

### Signup's own column on core's tables

`server_configs.signup_module_enabled`, the module's switch (`OWN_COLUMNS`), as `architecture.md`'s
"Each table is read and written by its owner alone" lets signup and weather keep theirs; `core.md`,
"The modules' own columns on core's tables", says how core keeps off it. One function reads it,
`signup_module_service.signup_enabled_on`, the switch reader signup registers with `ModuleService`
(`register_switch`) and the one read `READS_OF_OWN_COLUMNS` allows. It is written by the switch-on
(`module_switch_settings`) and the switch-off (`signup_off_change`), and nothing else.

The league's base role and driver role are core's (`server_configs.base_role_id`,
`driver_role_id`), read by signup through core's configuration service and never written by it.

### What a signup belongs to

**A signup belongs to an account, a season and a window,** never to a driver profile:
`signup_records` holds `discord_user_id`, `season_id` and `window_id`, and no column names a
profile. The signup that stands for a driver is chosen across every account core holds for them
(`driver_signup_on`, through core's `accounts_of_profile`), in `SIGNUP_PRECEDENCE_SQL`'s order, as
the specification's "Signup data" has it. The review, a correction and an approval act instead on
the latest signup of one account (`get_record`, `get_record_on`, `mark_approved_on`,
`set_total_lap_ms_on`); `driver_signup_on`'s docstring says the two choices differ on purpose.
Records are inserted, never overwritten, save that a correction updates the one it was asked of
(`save_record_on`; `SignupRecord`'s docstring).

Because no table of signup's refers to `driver_profiles`, a driver deleted or merged leaves signup
with nothing to delete or re-point, and signup signs up to neither `profiles_deleted` nor
`profiles_merged`. A change of a driver's current account moves only the held channel's wizard row
("How modules and core fit together", below).

### The wizard's states

Two state machines carry a signup, and each has one owner. **The driver's status is core's**
(`DriverState`: Pending Signup Completion, Pending Admin Approval, Awaiting Correction Parameter,
Pending Driver Correction, and the states either side), moved only through core's state machine
(`DriverService.transition_on`, and `write_transition`, which a close's `return_drivers` uses),
which refuses a move it does not allow (`InvalidDriverTransition`, caught
by name and read as the driver having moved on). **The wizard's state is signup's**
(`WizardState`): `UNENGAGED`, or one of the nine `COLLECTING_*` steps the wizard asks in turn.

| Driver's status | Wizard row |
|---|---|
| Pending Signup Completion | a `COLLECTING_*` state, the 24 hours counted from `last_activity_at` |
| Pending Admin Approval | `UNENGAGED`, the review panel standing in the channel |
| Awaiting Correction Parameter | `UNENGAGED`, `correction_offered_at` the moment the choices were posted |
| Pending Driver Correction | the `COLLECTING_*` state of the answer being re-collected |
| any, once the signup has ended | `UNENGAGED`, the row kept while its channel is held, `held_until` the end of the hold |

The wizard is `UNENGAGED` while it waits on a manager, so a restart's sweep never expires a signup
in review (`end_wizard_on`'s docstring), and the driver's status, not the wizard, is what tells a
driver awaiting a correction choice (`recover_correction_timeouts`' docstring). A row's next save as
a wizard clears `held_until` (`save_wizard_row_on`), so a signup that begins again is no longer held.
A row that names a channel is deleted only with that channel, a pack excepted: by the record of
the job that deletes the channel (`channel_delete_change`, `member_left_change`,
`DELETE_REPLACED_CHANNEL`), so the row names a channel only while the channel may stand. Two
deletions name no channel: a departing member's `leave` deletes a row that holds none, and
`driver_account_changed_on` deletes the new account's abandoned draft, which the re-keyed row
would otherwise collide with.

**The wizard asks against a frozen copy of the configuration.** `config_snapshot_json` is taken as
the wizard starts and holds the settings, the slots by their durable identity, the window's tracks
and the teams' names and shorthands; the wizard judges every answer against it, so a setting
changed mid-signup changes nothing for a driver part-way through. A slot's display ordinal is never
stored (`_snapshot_to_dict`'s docstring; `AvailabilitySlot`'s docstring, pinned by
`test_no_display_ordinal_is_ever_stored`).

### Who writes each table

| Table | Written by | What it writes |
|---|---|---|
| `signup_module_config` | `module_switch_settings` | a bare row, inserted only where none stands |
| | `channel_setting` | the channel; the Sign Up button's id where `/signup channel` posts the information message |
| | `signup_module_service` (`set_window_open_on`, `set_window_closed_on`, `set_close_at_on`) | the window opened, closed and its close time, for `window_changes`, `closing_steps` and `close_time_settings` |
| | `signup_module_service` (`delete_config_on`, `bot_packed_on`) | the row deleted by the switch-off; the ids of Discord objects cleared by a pack |
| `signup_module_settings` | `signup_settings` | the three settings |
| | `signup_module_service` | the closed notice's place, set by a close and cleared by an opening and a pack |
| `signup_availability_slots` | `signup_settings` | a slot added or removed |
| `signup_windows` | `signup_module_service` | a window opened, its close time, its closing; a season's windows deleted |
| | `signup_off_change` (`switch_off`) | the open window stamped closed |
| `signup_wizard_records` | `signup_module_service`, for `wizard_changes`, `wizard_answers` and `review_changes` | the row created and saved, ended, deleted, `correction_offered_at` |
| | `closing_steps` (`hold_until_on`) | `held_until`, in the record of the job that arms the deletion |
| | `signup_module_service` (`driver_account_changed_on`, `bot_packed_on`), `closing_steps.drivers_returned_on` and `window_changes`' `return_drivers` | the row re-keyed to a new account and the new account's abandoned draft deleted; every row deleted by a pack; a returned driver's wizard ended |
| | `held_channel_moves` (`DELETE_REPLACED_CHANNEL`) | the replaced account's row, deleted with its channel |
| `signup_records` | `signup_module_service`, for `wizard_answers` and `review_changes` | a signup saved or corrected, approved, its lap sum |
| | `signup_module_service` (`driver_turned_down_on`, `season_deleted_on`) | the approval withdrawn; a season's records deleted |
| `season_signup_config` | `signup_module_service` (`configuration_confirmed_on`, `season_deleted_on`) | the season's copy, written or replaced; deleted with the season |

Most writers are `SignupModuleService`'s `*_on` methods, each committing nothing, called by the
topic file whose change saves; a setting writes its own row in its `Setting`'s save. Every time a
writer stores is handed in, from the queue's clock (`now=lambda: bot.change_queue.now()` in
`wiring.py`): no writer reads a clock of its own and no column defaults to one, as
`architecture.md`'s "The time is always passed in" asks (`test_no_service_reads_the_wall_clock`,
`test_no_statement_reads_sqlites_clock`, `test_no_column_defaults_to_sqlites_clock`).

### The copies kept as history

`season_signup_config` and the tracks and close time on each `signup_windows` row are copies the
specification's "Signup data" requires kept with the season, so that a signup kept stays readable;
`signup_windows.opened_at` and `signup_records.window_id` are kept for the same section's rule that
a signup belongs to the window it came through. No code of the bot reads them back
(`get_season_config` and `get_windows` are called by tests alone, and nothing outside the record's
own reader reads `window_id`): they are the stated exception of `architecture.md`'s "A column nothing reads is removed,
not kept". The season's copy is taken in the configuration's confirmation, on its save, while
signup is on (`configuration_confirmed_on`, through core's `configuration_confirmed` hook;
`test_the_season_config_snapshot_holds_settings_and_slots`); each window's, as it opens and as its
close time is set.

The wizard's `config_snapshot_json` is not history: the wizard reads it for every answer (above).

### What disabling forgets and keeps

The switch-off's save (`signup_off_change`'s `switch_off`) calls `delete_config_on`, which deletes
the configuration row and so forgets the signup channel, the window's state and the Sign Up
button's id, since a channel can be deleted or given another job while the module is off. Nothing
cascades from it: the slots and the three settings sit in tables no key joins to it, and stand
again when the module is switched back on, as the specification's "Enabling signup flow" (the
disable bullet) requires (`delete_config_on`'s docstring;
`test_time_slots_and_settings_stand_again_after_a_re_enable`). The closed notice's place, kept
among the settings, is the one id the switch-off does not lose, so the first opening after a
switch-on still takes the notice down
(`test_the_closed_notice_is_kept_in_view_across_switching_signup_off_and_on`). The signups, the
windows, the seasons' copies and the wizard rows of held channels are kept too; the league's two
roles are core's and stand (`test_disabling_signup_keeps_both_roles`).

The switch-on writes a bare configuration row **only where none stands**, so a row that survived is
kept rather than blanked (`module_switch_settings`' docstring;
`test_re_enabling_signup_keeps_a_standing_row`).

### Links, and what stops a delete

No link of signup's stops a delete. `signup_records`, `signup_windows` and `season_signup_config`
refer to `seasons` with no delete action, and signup deletes its own rows of a season in the
delete's save, through core's `season_deleted` hook (`season_deleted_on`, records before the windows
they point at; `core.md`, "Each module deletes its own rows"). Inside signup the one action is
`signup_records.window_id`'s `ON DELETE SET NULL`. A pack clears the wizard rows and the ids of
Discord objects through `bot_packed` (`bot_packed_on`), leaving the module on with nothing
configured.

### What a restart loses

Two things, both held in memory under `architecture.md`'s rule on a confirmation's lapse ("Timed
work and restarts"), each a timer in memory and no change on the queue:

- **a manager's pending reason** after Reject or Request Changes, awaiting the message they type
  (`_PENDING_REASONS` in `signup_views.py`, with its five-minute lapse an asyncio task kept in the
  entry). A restart drops it unrecorded; `clear_pending_reasons` drops every entry and cancels every
  lapse on `/bot pack` and `/bot factory-reset` (`in_memory_state_cleared`;
  `test_a_reset_cancels_every_pending_lapse_and_none_fires_after`);
- **`/signup close`'s confirmation** (`ConfirmCloseView`, five minutes), whose cancel and lapse are
  recorded and whose asking records nothing.

Everything else is a row or a queue record: the wizard's answers, its timers' moments
(`last_activity_at`, `correction_offered_at`, `held_until`) and the close time, from which the
start-up steps find what a restart left to do. *Rejected:* keeping the draft answers in memory
until the last answer, which a restart would lose (`wizard_answers`' module docstring). Every
lapse that moves a driver's status is a one-off timer asking a change ("Timed work and restarts",
below).

---

## How the code is laid out

Answers to `architecture.md`, "How the code is laid out". Signup's code sits in `signup/cogs/`,
`signup/services/` and `signup/models/`, beside `signup/wiring.py`.

### Services, by topic

The services sit flat in `signup/services/`, each file a topic. The table groups them.

| Topic | Files | Holds |
|---|---|---|
| The service | `signup_module_service` | `SignupModuleService`, the reads and the `*_on` writers of every table, and signup's subscribers to core's hooks (the confirmation's copy, a driver's names, the turn-down, the account change, the season's delete, the pack, the review's section) |
| Settings | `signup_settings`, `channel_setting`, `close_time_settings`, `module_switch_settings` | the five `/signup` settings that are not the channel or the close time, and `open_to_change`, the refusal they share; `/signup channel`; the close-time group; the switch-on |
| The switch-off | `signup_off_change` | `module.off:signup` |
| The window | `window_changes`, `window_close`, `information_post` | `signup.open` and `signup.close`, and the window's close at a season's end; the close timer, its handler and its catch-up; the one post of the information message |
| The wizard | `wizard_changes`, `wizard_answers`, `wizard_service` | the start, the end, a held channel's deletion and a member leaving; the answers, the submission and the review panel; `WizardService`, the channel's making and lock, the prompts and the review panel's text |
| The review | `review_changes` | Approve, Request Changes, the correction choices and their lapse |
| A channel's closing | `closing_steps` | the jobs that close a driver's channel (notice, lock, deletion armed), `signup.closing`, the timers' names and asks, the window's closing jobs, and the wizards' start-up catch-up |
| Posting | `signup_posting`, `signup_views` | the one place signup posts (below); every view and button signup posts, with the pending reasons |
| Seams with core | `signup_prerequisites`, `held_channel_moves`, `channel_uses`, `fingerprint_areas` | what a season's confirmation and configuration lack; the held channel's move to a new account; the channels signup holds; signup's area of a season's fingerprint |

**Two service classes, the rest functions.** `SignupModuleService` carries the database path and the reads
and writes of the module's own rows; `WizardService` carries the bot, the queue and the signup
service for the channel's making and the wizard's words. `LeagueBot` holds them as
`signup_module_service` and `wizard_service`. Every change type is a factory the builder calls
with what it needs, the services mostly handed as getters read when the change runs (the comments
in `register_change_types` say why).

**The views live among the services** (`signup_views.py`), beside the jobs that post them, so a
signup job imports no cog (its module docstring). The entry point registers each as a persistent
view, so its buttons answer after a restart.

### The cogs

`/signup` is one group in one cog, `SignupCog` (`signup/cogs/signup_cog.py`): `channel`,
`nationality`, `time-type`, `time-image`, `open` and `close` on the group, and the subgroups
`config` (`view` alone, `test_the_signup_config_group_holds_only_view`), `time-slot`, `close-time`
and `unassigned`. A command that changes something parses its own input (the close time, through
`_parse_close_time`, one parser for `/signup open` and `/signup close-time`) and asks one change. A
setting command holds no rule of its own; `/signup open` and `/signup close` also refuse at the ask
(test mode, no configuration; signups not open, a season's end in hand, a close time armed), and
`close` chooses between asking at once and `ConfirmCloseView`, the change's check judging again
when its turn comes. Five commands run `_module_gate` after their tier guard: `time-slot list`,
`open`, `close`, `unassigned list` and `unassigned export`. The setting commands go straight to the
queue and leave the module-on refusal to their change's check: `open_to_change` for the five settings and
`/signup channel`, the close-time group's own check for its three.

The cog holds three listeners. `on_message` hands a message typed in a driver's channel to
`take_typed_answer`, which asks the driver's own `signup.answer` turn as the driver, after turning
away a message typed while an earlier answer waits and passing over one where the wizard asks
nothing. The other two ask as the bot and leave the judgement to the change's check:
`on_guild_channel_delete` asks a signup's ending where a wizard names the
channel deleted; `on_member_remove` asks `signup.member_left` for every member who leaves. Each
ignores a server not the league's.

`AdminReviewCog` (`admin_review_cog.py`) holds one listener, which hands a message that may be a
manager's reason to `take_reason`.

### The wiring and the models

`signup/wiring.py` is signup's part of the builder (`architecture.md`, "How the code is laid
out"): `register_switch`, `sign_up` (the hooks signup alone joins), one `sign_up_to_*` for each hook
several modules join, `register_change_types` (which registers the three timers of a signup too),
`register_scheduler_callbacks` (the close timer), `cancel_wizard_timers` and `start_up_steps`. It
holds no rule and no database code: each start-up step stands for a function of the services.

`signup/models/signup_module.py` holds plain data: `WizardState`, the configuration, the settings, a
slot, the configuration snapshot, a signup record and a wizard record.

---

## How a change is carried out

Answers to `architecture.md`, "How a change is carried out"; the specification says what a league
is told.

### The changes signup asks

| Kind | Lane | Asked by |
|---|---|---|
| `signup.setting.nationality`, `.time_type`, `.time_image`, `.time_slot_add`, `.time_slot_remove`, `signup.setting.channel` | league | the commands |
| `signup.close_time.add`, `.cancel`, `.modify` | league | the close-time group |
| `module.on:signup`, `module.off:signup` | league | `/module enable` and `/module disable` |
| `signup.open`, `signup.close` | league | `/signup open`; `/signup close` and its Confirm Close, the close timer, the restart |
| `signup.close_timer.arm` | league | the restart, for a close time still ahead |
| `signup.start`, `signup.end`, `signup.submit` | league | Sign Up; Cancel Signup, a Reject reason, the 24 hours, a channel found gone; the last answer |
| `signup.approve`, `signup.request_changes`, `signup.correction.choose`, `signup.correction.lapse` | league | the review's buttons; the five minutes' timer and the restart |
| `signup.member_left` | league | the member-remove listener |
| `signup.answer`, `signup.panel`, `signup.choices`, `signup.recollect`, `signup.closing`, `signup.reason.delete`, `signup.channel.delete` | the driver's, `channel:<id>` | a typed answer or a wizard button; asked back by the league's changes; the held channel's 24 hours |

Signup also contributes jobs to changes core owns, through the hooks of a season's end and a
driver's account changed (below).

### The two lanes

**Every change of a driver's status is the league's**, as the core specification's "How a change
is carried out" has it: a press outside a conversation is a command's, and so is a timer's change.
**The driver's own lane, `channel:<id>`, carries the conversation:** the answers, and what a league
change asks back of it to post in the driver's channel. So `signup.submit` saves the signup on the
league lane and asks `signup.panel` back on the driver's; an approval, a withdrawal and a Reject
ask `signup.closing` back (`closing_steps`' module docstring), while an expiry runs the notice, the
lock and the deletion's arming itself on the league lane and an ending for a channel gone asks
nothing; `signup.request_changes` asks `signup.choices`, and the choice asks `signup.recollect`. A refusal Discord gives there stops that driver's lane alone, and the league's
change is done (`test_a_closing_notice_asked_back_runs_on_the_driver_s_lane_and_a_refusal_stops_only_it`,
`test_the_last_answer_hands_the_submission_to_the_league_and_the_panel_comes_back_on_the_lane`).
*Rejected:* one change for the whole signup, which would hold a driver's lane until the league had
approved them (`wizard_answers`' module docstring).

**A correction's lapse hands its review panel to the driver's own lane.** The lapse is a timer's
change and the league's; its `post_panel` asks `signup.panel` of the driver's lane carrying the
lapse, whose job posts the panel and writes the lapse's record once it stands, so a refused panel
stops that driver's signup and no league change waits on it (`lapse_change`'s and `panel_change`'s
docstrings).

**A driver's-lane change declares what it writes** (`ChangeType.writes`), as `architecture.md`'s "A
conversation's lane writes only its own conversation's tables" has it: `signup_wizard_records`
alone for an answer, the choices, a closing and a held channel's deletion, nothing for the panel,
the recollection and a reason's take-down. The signup record and the driver's status are written by
the league changes that ask them back.

**An answer is judged on its turn, and counts from when it was typed.** The rule is the
specification's "Wizard flow"; its consequence in the code is that the `judge` job reads the wizard
as it stands when the turn comes, and only the 24 hours count from the moment of typing: an expiry
is not due while an answer typed inside them waits on the lane (`end_change`'s check;
`wizard_answers`' module docstring). A message typed while an earlier answer waits is turned away
(`typed_turns`). `judge_answer` and `judge_button` are pure: they read the wizard and the answer and
give what to keep or the refusal (`wizard_answers`' `Refused`, a result and no exception). A
correction choice is judged against the press's time, carried in its payload, against the wizard's
`correction_offered_at` (`review_changes`' module docstring;
`test_a_choice_pressed_in_time_counts_though_it_runs_after`).

### Saves are all or nothing

Each saving job writes what its step changes and its audit and lines in one save, on the
connection the queue hands it, and a job that posts records its id in the save that marks it done.
Three of the shape's consequences:

- **The window is open only once its message is up:** `post_info`'s record opens it, and
  `post_closed`'s record closes it and moves the season on (`window_changes`' module docstring), so
  no window is recorded open with no button or closed with its season left behind.
- **A driver's channel is named only once it is certain to stand:** `create_channel`'s record saves
  the wizard row, and a try that created a channel and failed to save keeps its id for the next try
  to delete (`wizard_changes`' module docstring).
- **The submission is one save:** the signup saved, the driver moved to Pending Admin Approval and
  the wizard parked together, the driver's name read first by a job of its own, since a save waits
  on no second connection.

### Settings are `Setting` changes

The five settings, the channel and the switch-on are each a `Setting`
(`core/services/queue/setting_change.py`); the close-time group's three are too, under kinds that do
not carry `.setting.`, since a close time repairs nothing and is not among the settings that overtake
a stopped lane (`close_time_settings`' docstring). The refusals are the check's, in the command's
order and words, made at the ask and again as the change starts. `open_to_change` holds the two
the five settings and the channel share (the module on, the configuration not fixed by a confirmed
season); the close-time group's check reads the switch itself and judges no fixed configuration,
and the switch-on refuses where the module is already on. **A slot is removed by its
identity, not its rank**, the command resolving the rank to a day and time at the ask
(`test_removing_slot_n_twice_removes_one_slot`).

**`/signup channel` is a setting whose check judges both channels before the save.** It refuses,
before anything is saved, where the bot lacks Manage Channel or Manage Permissions on the new
channel or on the old one it is to clear, so a move Discord would refuse for the old channel never
strips it of what it holds; the check, not the command, judges it, since a change ahead on the queue
may have taken the channel since the command asked. After the save, one step (`SET_PERMISSIONS`)
sets the new channel's overwrites first and clears the old one's last. Only a refusal the check
could not foresee (`discord.HTTPException`, caught by name) leaves the channel saved with the fault
named in the reply and beside the line; the old channel is cleared all the same, being no longer
the signup channel. It is a repair setting (`repairs_the_bot`): where the stored channel is no
longer on the server it is allowed though a confirmed season has fixed the configuration, and runs
while the lane is stopped. Where a window is open, a second step (`POST_INFORMATION`) posts the
information message with its Sign Up button in the new channel and keeps its id as the window's,
so drivers can sign up there at once; a fault in it leaves the channel saved and the reply says what
was not posted (`channel_setting`'s module docstring; the specification's "Enabling signup flow").
Pinned by `test_signup_channel_refuses_early_without_both_permissions`,
`test_moving_the_signup_channel_sets_the_new_before_clearing_the_old`,
`test_an_unforeseen_refusal_keeps_the_channel_and_names_the_fault`,
`test_a_signup_channel_deleted_mid_season_is_replaced` and
`test_a_signup_channel_replaced_while_signups_are_open_carries_the_sign_up_button`.

**The switch-on and the switch-off read the season's stage again as they start**, so one asked
before a season's configuration is confirmed and run after it is refused in the words the ask
gives (`configuration_fixed_refusal`;
`test_a_signup_switch_asked_before_the_configuration_is_confirmed_and_run_after_is_refused`). The
switch-off is a change of its own, not a `Setting`: where the window is open it runs a close's jobs
first, then `switch_off`'s save, then `clear_overwrites`, so the channel keeps what the module needs
until the module has gone (`signup_off_change`'s docstring;
`test_disabling_signup_closes_the_window_and_switches_the_module_off_as_jobs`).

### Timers are disarmed in a job after the save

A save that makes a timer's work no longer due never disarms it itself; the change plans a
`disarm_timers` job after it (`closing_steps.disarming`), as `architecture.md`'s "Timed work and
restarts" has it. Where the save asks a closing on the driver's lane, which arms the channel's
deletion, the league lane's disarm keeps that one timer (`keep_deletion`, through
`cancel_wizard_timers(bot, deletion=False)`), since it may run after the closing
(`test_the_closing_s_deletion_survives_the_disarm_whichever_lane_runs_first`). Likewise a timer is
armed by a job of its own after the save that made it due: the five minutes from the moment the
choices' record saved `correction_offered_at`, the 24 hours from the moment their job runs.

### The steps that cannot be discarded

A job whose dropping would leave a signup unsettled, which refuses the season's placements, is
refused its Discard and moved only by a Retry (`Step.undiscardable`, each with a reader saying what
the drop would leave):

| Job | Change | Dropped, it would leave | Pinned by |
|---|---|---|---|
| `end` | `signup.end` | a driver mid-correction never ended | `test_ending_a_signup_cannot_be_discarded` |
| `arm_inactivity` | `signup.answer` | a signup that never times out | `test_rearming_the_24_hours_cannot_be_discarded` |
| `commit` | `signup.submit` | a signup never submitted | `test_a_correction_s_commit_cannot_be_discarded` |
| `post_panel` | `signup.panel` | a driver awaiting approval with no panel | `test_the_review_panel_s_post_cannot_be_discarded` |
| `arm_lapse` | `signup.choices` | a correction request nothing ends | `test_arming_the_five_minutes_cannot_be_discarded` |
| `choose`, `arm_inactivity` | `signup.correction.choose` | a choice nobody can make, or a correction that never times out | `test_the_choice_and_its_24_hours_cannot_be_discarded` |
| `lapse`, `post_panel` | `signup.correction.lapse` | a correction nobody can choose, or no panel | `test_the_lapse_itself_cannot_be_discarded`, `test_a_lapse_s_panel_cannot_be_discarded_and_a_retry_records_the_lapse` |

**What they post is bounded by construction,** as `architecture.md`'s "Posting to Discord" asks of
a job that cannot be discarded. The review panel runs as long as the driver's answers do, so
`post_review_panel` divides it through `chunk_message` into messages Discord takes, the buttons on
the last; a try Discord refuses part-way keeps the ids of the parts sent, for the next try to remove
before it posts again (`test_the_worst_case_panel_is_posted_in_parts`,
`test_a_divided_panel_retried_posts_its_earlier_parts_once`). A panel no longer owed removes those
parts on a best effort and completes, so the undiscardable job never sticks
(`test_a_dropped_panel_whose_parts_discord_will_not_remove_still_clears`). A refusal quotes at most
`_QUOTED_AT_MOST` characters of what the driver typed
(`test_a_long_wrong_answer_is_refused_in_one_message`).

A job that may be discarded is named in the reply and the line where it is: a refused role at
approval is named and never a stop (`test_a_role_that_cannot_be_granted_is_named_and_does_not_stop_the_queue`).

---

## Timed work and restarts

Answers to `architecture.md`, "Timed work and restarts".

### The four timers

Each is a one-off timer armed through `SchedulerService.arm_once`, its callback registered by the
wiring, and each callback asks a change of the queue, as the bot, which reads the row again.

| Timer | Keyed by | Armed by | Its change reads | Missed while down |
|---|---|---|---|---|
| `signup.close_timer` | one key, `"window"` | `signup.open`'s `arm_close`, the close-time group, `signup.close_timer.arm` | the handler reads the stored close time against the queue's clock before it asks (`close_when_due`); `signup.close` then reads the window | the close is asked at start, or the timer armed again for a time still ahead (`recover_missed_close`) |
| `signup_inactivity` | the account | the start, each answer, a correction's choice | `last_activity_at` plus 24 hours, and an answer typed in time still waiting (`end_change`'s check) | the expiry is asked at start (`recover_wizards`) |
| `correction_lapse` | the account | `signup.choices`' `arm_lapse`, five minutes from `correction_offered_at` | the driver still Awaiting Correction Parameter, no choice pressed in time waiting (`lapse_change`'s check) | the lapse is asked at start for every driver awaiting a choice, as the specification's "Wizard flow" has a restart do (`recover_correction_timeouts`) |
| `signup_channel_delete` | the account | a closing's `arm_deletion`, or its `close_signup` at a season's end; the held channel's move | the wizard still naming the channel, `held_until` passed (`channel_delete_change`'s check) | the deletion is asked at start for each hold past its end (`recover_wizards`) |

A timer that fires early, or after its work changed, finds it not due and is dropped, so the row,
not the timer, says when work is due (`test_a_close_time_cancelled_does_not_close_signups`).

### The switch, and its stated exception

A timer's change finds no work due while signup is off. The expiry's check (`signup.end` for the
reason `expired`), the lapse's and `signup.close_timer.arm`'s each read the switch; the lapse's read
is pinned by `test_the_timeout_does_nothing_while_the_signup_module_is_disabled` and the expiry's by
`test_expiry_is_not_due_while_signup_is_off`. The close timer's handler and its start-up step read
the switch before asking anything, and `signup.close` finds no window once
the switch-off has forgotten the configuration.

**A held signup channel is still deleted on time after signup is switched off**, the stated
exception `architecture.md`'s "Timed work and restarts" names: the channel's driver has left the
wizard, its deletion was announced while the module was on, and nothing could arm it again later.
`signup.channel.delete`'s check reads no switch (`channel_delete_change`'s docstring), and the
switch-off disarms only the timers of wizards still engaged (`signup_off_change`'s docstring). Pinned
by `test_a_held_channel_is_deleted_while_signup_is_off` and
`test_switching_signup_off_keeps_a_held_channel_s_deletion`.

### The two start-up steps

Signup's turn of the start-up sweep is two steps named in `wiring.start_up_steps`, run only while
signup is on (`core.md`, "Start-up"), in this order:

1. **signup close timer** (`window_close.recover_missed_close`): a close time passed while the bot
   was down asks the close; one still ahead asks the timer's arming, so an arming a Discard dropped
   is made good;
2. **wizards** (`closing_steps.recover_wizards`): a signup whose channel was deleted while the bot
   was down is ended first (`recover_gone_channels`), each driver awaiting a correction choice has
   its lapse asked (`recover_correction_timeouts`), each hold past its end has its deletion asked,
   and each signup left unanswered for 24 hours has its expiry asked, every moment judged against
   the queue's clock.

Each asks the queue as the bot and posts nothing; the change's check drops what the driver has
since moved on from. They arm nothing themselves (the close timer's arming is a change they ask): a
deadline still ahead is a timer the scheduler's store keeps through the restart (`recover_wizards`'
docstring). Pinned by `test_a_close_time_missed_while_down_closes_on_start`,
`test_a_close_time_still_ahead_is_armed_on_start`,
`test_a_held_channel_due_while_down_is_deleted_on_start`,
`test_a_restart_asks_the_expiry_of_each_lapsed_signup`,
`test_a_restart_ends_a_signup_whose_channel_was_deleted_while_the_bot_was_down` and
`test_a_restart_asks_the_lapse_of_each_driver_awaiting_a_choice`.

---

## Posting to Discord

Answers to `architecture.md`, "Posting to Discord". Every message signup posts as a job, and every
driver's channel it deletes, goes through `signup_posting.py`, the one place it does so, which hands
it to core's handler for its kind and turns Discord's refusal into `StepFailedOnDiscord` with the
reason its caller gives. A message taken down (the Sign Up button, the closed notice, a manager's
reason) is taken down by core's `standing_posts.delete_replaced_step`; a driver's channel is created
by `WizardService._create_wizard_channel`, in the `create_channel` job.

| Post | Kind | Channel | Handler | Found again by |
|---|---|---|---|---|
| the information message with its Sign Up button | standing post | the signup channel | `post_standing_message`, pinging the base role | `signup_module_config.signup_button_message_id`, which also names the window a close was asked about |
| the "signups are closed" notice | standing post | the signup channel | `post_standing_message` | `signup_module_config.signup_closed_message_id`, and the settings' copy across a switch-off |
| the welcome, the questions, the refusals, the review panel, the choices, the closing notice | bot-owned channel | the driver's signup channel | `post` (`post_in_owned_channel`), naming who each may mention | the channel itself; a divided panel's parts are kept by the job for a retry |
| the driver's signup channel | bot-owned channel | in the signup channel's category | made by `WizardService`; deleted whole by `delete_channel` (`delete_owned_channel`) | `signup_wizard_records.signup_channel_id` |

**The information message and the closed notice are the window's two standing posts**, and the
channel always shows one or the other: an opening takes the notice down only once its message is up
(`delete_closed`), and a close takes the button down before it posts the notice
(`window_changes`' module docstring). One function posts the information message,
`information_post.post_information`, for `signup.open` and for `/signup channel`, so a window moved
to a new channel shows what an opened one does.

**A driver's channel is a bot-owned channel**, handled as `architecture.md`'s table of the kinds of
post ("Posting to Discord") has one. The review panel notifies users, never a group
(`test_the_review_panel_notifies_no_group`).

**A departure is a line, not a post:** `signup.member_left` writes it in its `leave` save, naming a
placed driver from their signup for the live season, as the specification's "Signup data" asks.

---

## How modules and core fit together

Answers to `architecture.md`, "How modules and core fit together".

### The hooks signup signs up to

Signup declares no hook of its own. Core declares them and signup subscribes in `wiring.py`; where
several modules join a hook the entry point calls the function in its order.

| Hook | Signup's function | What signup does |
|---|---|---|
| `starting` | `wiring.start_up_steps` | its two start-up steps (above) |
| `base_role_changed` | `SignupModuleService.move_base_role_overwrite` | carries the signup channel's overwrite to the new base role |
| `configuration_confirmed` | `configuration_confirmed_on` | the season's copy of the configuration, in the confirmation's save |
| `confirmation_refused`, `configuration_faults_read` | `signup_prerequisites.confirmation_refusals`, `configuration_faults` | what a season's approval and configuration lack while signup is on |
| `review_sections_read` | `review_section` | signup's section of a season review |
| `driver_names_read` | `driver_names` | a driver named from their signup |
| `driver_turned_down` | `driver_turned_down_on` | the approval of the driver's signup withdrawn |
| `driver_account_changed`, `driver_account_changed_steps` | `driver_account_changed_on`, `held_channel_moves.held_channel_steps` | the held channel's wizard row re-keyed in the save; its timer and permissions moved after it |
| `season_end_closes`, `drivers_returned`, `driver_returned_jobs`, `season_end_steps` | `closing_steps`, `window_changes.season_end_steps` | the window closed at a season's end, each returned driver's wizard ended, told and their channel closed |
| `season_deleted`, `bot_packed` | `season_deleted_on`, `bot_packed_on` | see "The database" |
| `channel_uses_read`, `setting_channels_read` | `channel_uses` | the channels signup holds, whatever its switch |
| `fingerprint_areas_read` | `fingerprint_areas.fingerprint_areas` | its channel, window, settings and slots in the season's fingerprint, whatever its switch |
| `in_memory_state_cleared` | `signup_views.clear_pending_reasons` | drops the pending reasons |

Signup's switch is the keyed hook `ModuleService.register_switch` under `"signup"`. Three of the
subscribers that write in a save core calls them in whatever the switches say read signup's switch
themselves and do nothing while it is off: the confirmation's copy (`configuration_confirmed_on`),
the turn-down (`driver_turned_down_on`) and the account change (`driver_account_changed_on`;
`test_a_held_channel_stays_while_the_signup_module_is_off`). A season's end returning its drivers
(`drivers_returned_on`), the season's delete and the pack write whatever the switch says, reading
none. The names a signup gives, the channels and the fingerprint read none either, as their
docstrings say (`driver_names`, `channel_uses`, `fingerprint_areas`): a signup kept still names its
driver, and rows and channels held are held off or on.

### Core holds no rule of signup's

**Core reads what signup lacks for a season only through the two hooks signup signs up to**
(`signup_prerequisites`): `confirmation_refusals` refuses a season's approval while the signup
channel is unset, and `configuration_faults` names every fault of signup's part of the configuration
(the channel, the league's two roles, judged on the server). Both read signup's switch and hand
nothing while it is off, and neither catches a fault in reading, which reaches the caller's own
failure path. The approval judges the channel alone, since confirming the configuration already
judged the roles and fixed them (`signup_prerequisites`' module docstring). Core's season checks
neither import signup (the `core-uses-no-module` contract) nor name its tables (`TABLE_OWNER` in
`tests/repository/test_architecture_rules.py`). Pinned by
`test_the_signup_module_lacks_only_its_channel_while_it_is_on` and
`test_the_builder_signs_signup_up_to_a_confirmation_refused_after_results`.

### What signup reads of core

A driver's status and profile through `DriverService`, the accounts a driver holds through
`accounts_of_profile`, drivers by state through `drivers_in_states`, the live season's stage through
`live_season_stage_on`, the league's roles and tiers through `ConfigService`, and the track registry
through `track_service`. A league manager's tier at the review's buttons is `_may_review_signup`'s
one question of core's `is_league_manager` ("Errors and failures", below).

### The dependency table and the image module

Signup needs no other module (`NEEDS`, `core.md`, "The dependency table") and uses none: the
`signup-uses-no-other-module-but-image` contract allows the image module, and signup imports
nothing of it. Core names a driver from their signup through the `driver_names_read` hook above.

---

## Errors and failures

Answers to `architecture.md`, "Errors and failures".

| Starting point | Signup's part |
|---|---|
| A command, button or form | the base classes and `report_failure`; a refusal is `refuse`, one reply and one line |
| A change | its check refuses a member's request and drops the bot's where the work is no longer due; a job that fails stops its lane, with Retry and Discard |
| A timed job | the scheduler's job runner: each callback asks its change |
| A start-up step | the sweep's containment (`core.md`, "Start-up"); inside `recover_gone_channels` and `recover_correction_timeouts`, one driver's ask failing stops none of the others, while `recover_wizards`' own loops over the holds and the expiries catch nothing |

**Refusals are raised and caught by name.** A setting's save raises core's `Refused` where what its
check judged has gone (`NOT_CONFIGURED`, a slot no longer there); a driver's transition the state
machine refuses is `InvalidDriverTransition`, caught by name and read as the driver having moved on
(`test_the_state_machine_s_refusal_is_read_as_the_driver_having_moved_on`); a Discord refusal is
caught as `discord.HTTPException` (or `discord.NotFound` where a channel already gone is nothing to
do) and turned into `StepFailedOnDiscord` for the queue to stop on, or named where the job's rule
says so.

**`_may_review_signup` has no catch-all.** The review panel stands in the driver's own channel, so
the tier check is all that keeps a driver from approving themselves; a fault reading the
configuration reaches the press's handler, `LeagueView.on_error`, which tells the member and records
the failure, and grants nothing. Read as a refusal, the fault would hide behind "Insufficient
permissions"; read as a grant, anyone could act on a review (its docstring;
`test_a_signup_review_refuses_when_the_config_cannot_be_read`).

**The catch-alls** sit in the places `architecture.md` allows: the press that asks a manager's
reason as a change hands any fault to `report_failure` itself (`signup_views`, place 1); the
wizards' start-up catch-up asks each driver's ending and lapse in a loop that one failure never
stops (`recover_gone_channels`, `recover_correction_timeouts`, place 2). The switch-off's
`clear_overwrites` is one job of a queued change that clears each overwrite in a loop, logging and
stepping over one it cannot clear, for the reason `signup_off_change`'s module docstring gives: a channel with a stale overwrite is
put right by hand, a module left half-off is not. The architecture check accepts it as a catch-all
inside a loop; it is not one of the six places as `architecture.md` words them.

**What a restart does to interrupted work** (the core specification's "When the bot stops"):

- **a change cut off** is finished by the queue from its first job not done, so a close cut off
  after returning its drivers still tells each and closes their channels once;
- **a close time, an expiry, a correction's lapse or a hold that fell due** is caught up, as above;
- **a signup whose channel was deleted** while the bot was down is ended;
- **the buttons** of every signup view answer again, each registered as a persistent view.

---

## Constraints a later reader might tune away

Each is stated where it lives and pinned by the test named.

| Constraint | Stated in | Pinned by |
|---|---|---|
| A season has one open window | the index's comment in `001_baseline.sql` | `test_a_second_open_window_is_refused` |
| The switch-off keeps the slots and settings, and the closed notice's place | `delete_config_on`'s docstring | `test_time_slots_and_settings_stand_again_after_a_re_enable`, `test_the_closed_notice_is_kept_in_view_across_switching_signup_off_and_on` |
| The switch-on creates a configuration row only where none stands | `module_switch_settings`' docstring | `test_re_enabling_signup_keeps_a_standing_row` |
| A slot is removed by its identity, not its rank | `signup_settings`' docstring | `test_removing_slot_n_twice_removes_one_slot` |
| A driver's availability is stored by slot identity, never by ordinal | `AvailabilitySlot`'s docstring | `test_no_display_ordinal_is_ever_stored` |
| `/signup channel` judges the bot's permission on both channels before the save | `channel_setting`'s docstring | `test_signup_channel_refuses_early_without_both_permissions` |
| The new channel's overwrites are set before the old one's are cleared | `channel_setting`'s docstring | `test_moving_the_signup_channel_sets_the_new_before_clearing_the_old` |
| A signup channel gone is replaced though the configuration is fixed | `channel_setting`'s docstring | `test_a_signup_channel_deleted_mid_season_is_replaced` |
| A channel replaced while signups are open carries the Sign Up button | `channel_setting`'s docstring | `test_a_signup_channel_replaced_while_signups_are_open_carries_the_sign_up_button` |
| A held channel is deleted on time with signup off | `channel_delete_change`'s docstring | `test_a_held_channel_is_deleted_while_signup_is_off`, `test_switching_signup_off_keeps_a_held_channel_s_deletion` |
| The league lane's disarm leaves a closing's deletion standing | `cancel_wizard_timers`' docstring | `test_the_closing_s_deletion_survives_the_disarm_whichever_lane_runs_first` |
| A notice refused on the driver's lane stops that lane alone | `closing_steps`' docstring | `test_a_closing_notice_asked_back_runs_on_the_driver_s_lane_and_a_refusal_stops_only_it` |
| The undiscardable jobs | each job's `undiscardable` reader | the tests of "The steps that cannot be discarded" |
| The review panel is divided, and a retry posts its parts once | `post_review_panel`'s docstring | `test_the_worst_case_panel_is_posted_in_parts`, `test_a_divided_panel_retried_posts_its_earlier_parts_once` |
| A correction's choice is judged at the press | `review_changes`' docstring | `test_a_choice_pressed_in_time_counts_though_it_runs_after` |
| A restart releases every driver awaiting a correction choice | `recover_correction_timeouts`' docstring | `test_a_restart_reverts_even_within_the_five_minutes` |
| The close timer closes only where the stored close time has come | `close_when_due`'s docstring | `test_a_close_time_cancelled_does_not_close_signups` |
| A fault reading the configuration at a review's button grants nothing | `_may_review_signup`'s docstring | `test_a_signup_review_refuses_when_the_config_cannot_be_read` |
| Core's season checks read signup only through its hooks | `signup_prerequisites`' docstring | `test_the_signup_module_lacks_only_its_channel_while_it_is_on` |
| A pending reason is cleared, and its lapse cancelled, on a pack or reset | `clear_pending_reasons`' docstring | `test_a_reset_cancels_every_pending_lapse_and_none_fires_after` |
