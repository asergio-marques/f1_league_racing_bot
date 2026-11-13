# Attendance — why the code is shaped as it is

This file says how the attendance module's code is laid out and why: its tables, its services and
its command group, its changes on the queue, its timed work and start-up, what it posts, how it
meets core, results and the image module, how it fails, and the constraints a later reader might
tune away. Every path is under `src/leaguebot/` unless it says otherwise.

It cites rules rather than restating them. What binds every module is `architecture.md`'s, cited by
its section heading; core's side of a seam is `core.md`'s; what a league sees is the attendance
specification's (`docs/wip-specs/attendance_module_specification.md`), cited by its section heading.
A constraint on one function lives in that function's docstring, pinned by its test, and is named
here only where the shape depends on it. Where this file and the code disagree about what the code
does, the code wins, and the file is corrected at close-out.

---

## The database

Answers to `architecture.md`, "The database".

### Attendance's tables

Attendance owns five tables (`TABLE_OWNER` in `tests/repository/test_architecture_rules.py`), all
declared in `core/db/migrations/001_baseline.sql`. Every one of them is written, as the bot runs, on
a connection the change queue or a core hook hands in.

| Table | One row is | Held to it by |
|---|---|---|
| `attendance_config` | the module's switch and its eight settings: the three lead times of the check-in (`rsvp_notice_days`, `rsvp_last_notice_hours`, `rsvp_deadline_hours`), the three penalties, and the autoreserve and autosack thresholds (null where off) | `CHECK (id = 1)`; every value but the thresholds is `NOT NULL` with a column default |
| `attendance_division_config` | one division's check-in channel, its attendance channel and the id of its live sheet | keyed by the division |
| `driver_round_attendance` | one driver's attendance of one round of one division: the answer and when it was accepted, the reserve's placement (`assigned_team_id`, `is_standby`), whether the driver attended, and the points that attendance conferred and the total after them | `UNIQUE (round_id, division_id, driver_profile_id)` |
| `rsvp_embed_messages` | a round's check-in call and what follows it: the call's message and channel, the last notice's and the distribution's, and `deadline_run_at`, the mark that the deadline has run | `UNIQUE (round_id, division_id)` |
| `attendance_pardons` | one pardon of one kind (`NO_RSVP`, `ABSENT` or `NO_SHOW`) on one attendance row, with its justification and who granted it | `UNIQUE (attendance_id, pardon_type)`, `CHECK` on the kind; goes with its attendance row by cascade |

The switch is `attendance_config.module_enabled`, in attendance's own table, as `architecture.md`'s
"Each module turns itself on and off" has a module keep it. `attendance_enabled_on` reads it on a
connection, and the switch reader attendance registers with `ModuleService` is that function
(`register_switch`). A hook's subscriber and a change's check that need the switch read it
themselves, so none depends on the order the modules were built in.

### Attendance's own column on core's tables

`rounds.checkin_cleared`: one once no call is owed, either because the check-in clean-up has run or
because the call was given up with its deadline passed. It is attendance's alone (`OWN_COLUMNS`),
and core reads none of it: what it means for a round is attendance's rule, and a round's status,
which is core's, is read and moved through core's functions. Attendance reads it in its own
services (`READS_OF_OWN_COLUMNS`).

### Who writes each table

A table is written by its owner alone; inside the owner, the writing is spread across the topic
files, each writing what its topic changes. The table names each table's writers and what they
write.

| Table | Written by | What it writes |
|---|---|---|
| `attendance_config` | `module_switch_settings` | the switch-on: the row is replaced naming only the flag (below) |
| | `attendance_settings` | one column for each of the eight settings, in the setting's save |
| | `attendance_config_service` (`switch_off_on`) | the flag lowered, the settings kept |
| `attendance_division_config` | `division_channels` | each channel, by an upsert naming its one column, so setting one never blanks the other |
| | `attendance_sheet` (`record_sheet_on`) | the live sheet's message id |
| | `attendance_config_service` (`switch_off_on`) | every row deleted, so a returning module posts into no stale channel |
| | `attendance_hooks` | a deleted division's row; every sheet id cleared when the bot is packed |
| `driver_round_attendance` | `attendance_config_service` | a round's rows as its call is posted (`bulk_insert_attendance_rows_on`) and a driver's answer (`upsert_rsvp_status_on`, `answer_rsvp_on`) |
| | `rsvp_service` | the reserves' placement, made at the deadline and cleared when an amended round reopens its check-in, and a round's answers cleared when a call is given up (`clear_check_in_answers_on`) |
| | `check_in_changes` | the answers of drivers the division no longer holds, deleted when a call replaces one |
| | `attendance_scoring` | `attended`, `points_awarded` and `total_points_after` |
| | `attendance_hooks` | a deleted round's, a deleted driver's and a merged driver's rows |
| `rsvp_embed_messages` | `attendance_config_service` | the call's row, and the ids of the last notice and the distribution |
| | `rsvp_service` | `deadline_run_at` |
| | `check_in_changes` | a message forgotten as it comes down, a row deleted with its call |
| | `attendance_hooks` | a deleted round's rows; every row cleared when the bot is packed |
| `attendance_pardons` | `attendance_pardons` | a round's pardons held, rewritten and put back by an amendment |
| | `attendance_after_review` | the pardons of a round's first approval |
| `rounds.checkin_cleared` | `check_in_changes` | raised when the check-in is closed |
| | `rsvp_service` | lowered when an amended round reopens its check-in |

The files are the topics the module's work falls into, and a writer sits in the file whose topic it
is, not beside every table it touches: `architecture.md` asks that the owning module write its
table, not that a single file do.

### The stored copies of derived figures

Attendance keeps three columns on `driver_round_attendance` that are a copy of something worked out
from other rows: `attended` (from the round's results), `points_awarded` (from the answers held,
the pardons and the penalties set) and `total_points_after` (the driver's running total through the
round). A driver's total is the sum of their rounds, but the sheet and the sanctions read the copy,
so a copy left behind would publish a figure worked out from an old one.

One function recomputes them, as `architecture.md`'s "The database" has a stored copy kept:
`attendance_recompute._recalculate_forward`. It rebuilds the round asked, as much of its `attended`
flags as the caller asks for (`recompute`: `"all"`, `"round"` or `"none"`, its docstring says what
each leaves alone), distributes the points of the round and carries the total through every
finalised round after it, so that a stop leaves the copies of all the rounds as they were or all of
them carried. Its three callers:

| Caller | Mode | Pinned by |
|---|---|---|
| `AttendanceAfterReview.record_on`, a round's first approval | `"none"`, the flags having been recorded upgrade-only | `test_recording_a_round_carries_every_later_round_s_total_on_the_save_it_is_handed` |
| `AttendanceAfterReview.recalculate_on`, an amendment's approval | `"round"` | `test_the_amended_round_s_attendance_is_rebuilt_both_ways_on_the_save_it_is_handed` |
| the sync's save (`attendance.sync`) | `"all"` | `test_a_sync_recalculates_from_the_named_round_on_and_leaves_earlier_rounds_alone` |

Neither the recompute nor the distribution it calls opens a connection or commits: each is handed
the save's, so a call handed none is refused by the type check and not lost on close.

*Rejected:* one total for the season in place of a total at each round: each round's copy is kept
so that a figure that looks wrong can be traced round by round, which a flattened one cannot be
(`distribute_attendance_points`' docstring). *Rejected:* a recompute whose steps each open and
commit a connection of their own, which a stop leaves right up to one round and stale from the
next (`_recalculate_forward`'s docstring).

### The packaged defaults are written once

The module's packaged settings are the column defaults of `attendance_config`. The switch-on's save
writes `INSERT OR REPLACE INTO attendance_config (id, module_enabled) VALUES (1, 1)`, naming the
flag alone and leaving the eight values to the schema, so a module turned off and on again starts
from the packaged values, not from the ones it was left with, and the defaults live in one place.
`get_config_or_defaults` reads the same defaults off the schema where no row is stored, so a read
creates no row. A read hook, the window core asks for when a round is armed, is a reader and writes
nothing (`attendance_hooks.round_window`). Pinned by
`test_re_enabling_attendance_starts_from_the_packaged_defaults`,
`test_enabling_attendance_leaves_the_schema_s_own_defaults` and
`test_reading_a_round_s_window_writes_nothing`.

### Links, and what stops a delete

Attendance's tables refer to `rounds`, `divisions`, `driver_profiles` and `team_instances` with no
delete action, so every path that removes one of those removes attendance's rows first, which is
what attendance signs up to core's delete hooks for. Inside attendance only the pardons go by
cascade, from their attendance row.

| Hook | `attendance_hooks`' function | What it does |
|---|---|---|
| `rounds_deleted` | `rounds_deleted_on` | deletes the rounds' attendance rows, with their pardons, and their call rows |
| `divisions_deleted` | `divisions_deleted_on` | deletes the divisions' channel rows |
| `profiles_deleted` | `profiles_deleted_on` | deletes the drivers' attendance rows, with their pardons |
| `profiles_merged` | `profiles_merged_on` | moves the absorbed driver's attendance rows onto the one kept |
| `bot_packed` | `bot_packed_on` | clears the call rows and every sheet id, so that nothing edits a message of a server the bot has left |

Each works on the delete's own connection and commits nothing, so the rows go with the thing they
belong to or not at all (`architecture.md`, "Deletes are proven by a test, and each module deletes
its own rows"). Turning attendance off is another delete of attendance's own: it lowers the flag and
clears the divisions' channels but keeps the settings and the answers (`switch_off_on`).

### What a restart loses

Nothing the database holds. A call, its answers, the reserves' placements and the marks that the
deadline has run and the check-in is over are rows. What a restart leaves to be done is found from
them, or from the queue's records, by the start-up steps ("Timed work and restarts"). The buttons of
a call are in the memory of the process: they are made again at start-up for every call that stands.

---

## How the code is laid out

Answers to `architecture.md`, "How the code is laid out". Attendance's code sits in
`attendance/cogs/`, `attendance/services/` and `attendance/models/`, beside `attendance/wiring.py`.

### Services, by topic

Attendance's services are one folder of module functions, grouped by topic and named for it. The
table names a service by its file.

| Topic | Services | Chiefly writes |
|---|---|---|
| The check-in's answers and rows | `attendance_config_service` (`AttendanceService`: the settings read, a round's rows, an answer decided and written, the call's rows, and `rsvp_lock` and `derive_checkin_deadline`, the rules of the lock and of the deadline), `check_in_answer_changes` (an answer asked as a change, and the form's paste) | `driver_round_attendance`, `rsvp_embed_messages` |
| The call and what follows it | `check_in_changes` (the call, the last notice, the deadline and the clean-up as changes, the amendment's, the cancellation's and the advance's steps, and what a restart asks), `rsvp_service` (the embed built, the roster, the reserves distributed, the last notice's text, the call's graphic), `check_in_record` (the audit of a round's check-in, read before a call comes down) | `rsvp_embed_messages`, `driver_round_attendance` (the reserves), `rounds.checkin_cleared` |
| Scoring | `attendance_scoring` (who attended and the points a round confers), `attendance_recompute` (the one recompute of the stored copies, and what stands in the way of a recalculation), `attendance_pardons` | `driver_round_attendance`, `attendance_pardons` |
| Sanctions | `attendance_sanctions` (who is owed one and its save), `attendance_sync_change` (`/attendance sync`, its cards among its jobs) | none of its own: the sanction is core's placement written on the handed connection |
| The sheet | `attendance_sheet` (the rows, the graphic and its posting), `sheet_posting` (the `post_sheet` job, and the opening and final sheets) | `attendance_division_config` (the sheet's id) |
| Settings | `attendance_settings` (the eight), `channel_settings` and `division_channels` (the two channels), `module_switch_settings` (the switch) | `attendance_config`, `attendance_division_config` |
| What core's hooks ask | `attendance_hooks` (the deletes, the round's window, the review's section and lines), `attendance_after_review` (the review's share, below), `cancellation_notice`, `test_mode_steps`, `fingerprint_areas` | the rows of the delete hooks |
| Start-up | `check_in_recovery` | none: it asks the queue |

`check_in_changes` holds the call, its last notice, its deadline and its clean-up in one file: they
share one record, the call's row.

**Every service is module functions** but two classes. `AttendanceService` carries the database path
and the reads and writes of the check-in's own rows. `AttendanceAfterReview` carries the bot (for
Discord) and the services results' protocol needs, and implements it. `LeagueBot` holds them as
`attendance_service` and `attendance_after_review`; every other service is a function handed what it
needs when the builder registers it. The jobs and the hooks are handed the services they use
(`architecture.md`, "Services stay attached to the bot"), so that a job never looks one up on the
bot, and a function that needs Discord is handed the bot for that.

`derive_checkin_deadline`, the moment a round's check-in closes, stays in
`attendance_config_service`, where the image module draws the deadline from it.

### The cog

`/attendance` is one group in one cog, `AttendanceCog` (`attendance/cogs/attendance_cog.py`), with
three subgroups: `config` (the eight settings and `show`), `channel` (`rsvp` and `attendance`) and
`test` (`rsvp`, the maintainer's form); `sync` and `post-check-in` sit on the group. A command
judges what it can refuse before anything is asked, in the words the specification gives, asks the
change, and holds no rule of its own: each of the eight settings is one call, `_ask_setting`,
carrying the value, and the rules that compare one setting with another are the service's ("How a
change is carried out", below).

The check-in's buttons are the cog layer's too. `RsvpView` and its buttons sit beside
`handle_rsvp_button`, the one function that reads the id a button carries
(`rsvp_{action}_r{round_id}`, written in `_RsvpButton` and read in `handle_rsvp_button`, the only
two places that know the format). A service that posts or redraws a call is handed a way to build
the view (`CheckInViewFactory`), by `wiring`, as a service may not import a cog. The entry point
registers one `RsvpView` as a stub, and the start-up step "RSVP views and deadlines" arms the view
of each standing call.

### The wiring and the models

`attendance/wiring.py` is attendance's part of the builder (`architecture.md`, "A module's wiring"):
`register_switch`, `sign_up` (the hooks attendance alone joins, none at present), one `sign_up_to_*`
for each hook several modules join, `register_change_types`, `register_scheduler_callbacks` and
`start_up_steps`. It holds no rule, no database code and no clock: each start-up step stands for a
function of `check_in_recovery`, handed the bot and its queue, and every `now` a change type or a
hook is handed is the queue's clock (`bot.change_queue.now`).

`attendance/models/` holds plain data: the dataclasses of four of its tables, the names of the
check-in's four kinds of change, and `RecoveryFailed` (below, "Errors and failures").

---

## How a change is carried out

Answers to `architecture.md`, "How a change is carried out"; the attendance specification says what
a league is told.

### The changes attendance asks

| Kind | Asked by | Jobs |
|---|---|---|
| `attendance.check_in.call` | the `rsvp_notice` timer, the restart, `/attendance post-check-in`, an amendment | `give_up`, `post_call`, `delete_earlier` |
| `attendance.check_in.last_notice` | the `rsvp_last_notice` timer | `post_last_notice` |
| `attendance.check_in.deadline` | the `rsvp_deadline` timer, the restart | `distribute`, `lock_call`, `announce`; or `close_without_call` |
| `attendance.check_in.clean_up` | the `rsvp_cleanup` timer, the restart | `plan`, `delete_message` for each message, `mark_cleared` |
| `attendance.check_in.answer` | a press of a button, the maintainer's form | `save_answer` (or `save_answers`), `redraw` |
| `attendance.sync` | `/attendance sync` | the reserve's capacity, one save, the roles, the cards, lineups and sheets, the closing line |
| `attendance.setting.<name>` for the eight settings, `attendance.setting.rsvp_channel`, `attendance.setting.attendance_channel`, `module.on:attendance`, `module.off:attendance` | the commands | one save: the value, its audit entry and its log line |

Attendance also contributes jobs to changes core owns, through the hooks of a round amended, a
cancellation and a season approved or completed: the reopening of a round's check-in inside the
amendment's save, the take-down of a call and the reading of its record before a cancellation, and
attendance's own notice of the cancellation (`NOTIFY_CHECKIN`).

### Settings are `Setting` changes

Each of the eight settings, the two channels and the switch is a `Setting`
(`core/services/queue/setting_change.py`): its check, its save on a connection, its reply and the
tables it writes. The value, its audit entry and its log line are one save, stamped by the queue's
clock; a value already held writes nothing and says so. The refusals are the check's, in the
command's order and words, made at the ask and again as the change starts, since a change ahead of
it may have moved the season or another setting on.

**The rules that compare one setting with another are the service's** (`architecture.md`, "Where
a rule goes"): the timing invariant (the first notice before the last, the last before the
deadline: `validate_timing_invariant`) and the exclusion of autosack and autoreserve, judged in the
setting's check; settings run one at a time on the lane, so what the check reads is what the save
finds. The refusal of a timing once a season's placements are confirmed compares no setting and is
the change's check too. Pinned by `test_set_setting_refuses_a_timing_that_breaks_the_invariant`,
`test_set_setting_refuses_an_autosack_and_autoreserve_that_overlap` and
`test_set_setting_refuses_a_timing_change_once_a_season_is_confirmed`. A threshold of 0 is carried
as `None`, as the column stores it.

### The sync is one change

`/attendance sync` recovers a run of sanctions that did not all apply. It is one change whose jobs
are ordered by what each may fail at:

1. **The reserve's capacity,** read before the save, because the guards an autoreserve's move meets
   read templates off the loop, which a save may not wait on. It concerns the team and not the
   driver, so one answer serves every autoreserve of the run.
2. **One save:** the rounds the specification names are recalculated from their results
   (`recompute="all"`), then the sanctions the latest of them owes are applied, through core's
   placement functions on the same connection, so a stop leaves both done or neither. Who is owed
   is read there, on the totals just written. A sanction that cannot apply is named and the next
   driver taken (`SanctionNotApplicable`, or core's `Refused`), as the specification's
   "Resynchronising attendance" has it.
3. **The roles** of each driver sanctioned, ending on a fault rather than stopping the lane: the
   sanction stands, and the fault goes to the host's log.
4. **The cards, the lineups and the sheets,** results', core's and attendance's own jobs, which stop
   the lane where Discord refuses them.
5. **The closing line,** written last, so that it carries what a discard left undone.

The cards, lineups and sheets are jobs of the change and not work done after it, so a stop between
the sanctions and their cards is finished by the queue when the bot is back. A second sync plans no
card for what the first announced, since what it owes is read off the placements the first saved.
Pinned by `test_a_stop_between_the_sanctions_and_their_cards_finishes_on_restart` and
`test_a_second_sync_announces_nothing_already_announced`.

The command's refusals (the specification's "Resynchronising attendance") are the change's check
(`sync_verdict`), made before the ask and again as the change starts.

### The answer is judged when it is pressed

A press asks `attendance.check_in.answer`, and **the lock is judged at the moment the press was
made, never at the moment its turn comes** (Constitution XIII). A driver who pressed in time has
answered in time, though the queue reaches the press after the deadline. The check judges it as it
is asked and again as it starts; the save judges it a third time with the write (`answer_rsvp_on`),
on the answer the driver then holds, since that decides the lock of a reserve who has accepted. One
change at a time on the lane is what makes the second press wait for the first. A burst of answers
to one call makes one redraw: the answer whose turn comes with a later one to the same call in hand
leaves the drawing to that one. Pinned by
`test_an_answer_pressed_before_the_lock_counts_though_it_runs_after`,
`test_an_answer_pressed_after_the_lock_is_refused` and `test_a_double_press_makes_one_redraw`.

The maintainer's form asks the same kind with every entry of a paste, judged by no lock, as a
rehearsal sets any state, and its answers are saved at the queue's clock.

### The steps that cannot be discarded

A discard of a job lets the queue go on past a stop, so a job whose dropping would leave the bot
wrong is refused its discard and moved only by a retry. Two of attendance's are: `distribute`, which
places the reserves (dropped, they would never be placed) and `mark_cleared`, which marks the
round's check-in over (dropped, the next restart would take the round's answers for a call whose
messages had not been cleared and delete them). Pinned by
`test_placing_the_reserves_cannot_be_discarded` and
`test_marking_the_check_in_over_cannot_be_discarded`. A refused announcement, lock or deletion
stops the queue and may be discarded; a discarded announcement leaves the placements, and is not run
again at a restart, since the call's mark says the deadline has run.

---

## Timed work and restarts

Answers to `architecture.md`, "Timed work and restarts".

### The four timers

A round's check-in has four timers, `rsvp_notice`, `rsvp_last_notice`, `rsvp_deadline` and
`rsvp_cleanup`, armed by core's scheduler service (`schedule_attendance_round`, and
`schedule_round_cleanups` for the next-day clean-up) from the three lead times attendance's
`round_window` hook hands core. Attendance registers a callback for each kind (`register_timer`); a
callback reads the round again and asks the matching change, as the bot's, so a timer that fires
after its work was changed does nothing. All four are armed with no lateness limit, and each
handler judges lateness itself (`architecture.md`, "Timed work and restarts"). Pinned by
`test_a_check_in_timer_is_armed_with_no_lateness_limit`.

What each does when its moment passed while the bot was down is the attendance specification's
("RSVP notices"); the table says which function judges it, and against which moment.

| Moment missed | Judged by | Judged |
|---|---|---|
| the call | `give_up` | at the moment the change runs, against the deadline |
| the last notice | `_notice_not_due`: asked before the late call it reminds of has posted, it waits behind the call and is judged again as it starts | at the ask and as the change starts, against the deadline |
| the deadline | `_deadline_not_due` | as the change starts, against the call's mark that the deadline has run |
| the clean-up | `_not_cleaned` | as the change starts, against the round's mark that the check-in is over |

**A last notice past its deadline, or with no call standing, is a change dropped as not due**, with
a line in the host's log; a call past its deadline is given up instead, which closes the round's
check-in and tells the log channel (`give_up`). Pinned by
`test_a_last_notice_reached_after_the_deadline_is_dropped` and
`test_a_last_notice_with_no_call_standing_is_dropped`.

**Test mode's rule** (the core specification, "Test mode") is carried by judging lateness at the
ask, where a timer fires (`_left_to_advance`, `ctx.change_id is None`): a request accepted on time
is never dropped as the change starts, for the delay of a stopped or busy lane, while one whose
moment passed with the bot down is left to `/test-mode advance`. Pinned by
`test_a_call_timer_overdue_at_a_restart_under_test_mode_posts_no_late_call`,
`test_a_last_notice_timer_overdue_at_a_restart_under_test_mode_posts_nothing` and
`test_a_timer_fired_on_time_under_test_mode_is_posted_however_late_the_queue_starts_it`.

### The three start-up steps

Attendance's turn of the start-up sweep is three steps named in `wiring.start_up_steps`, each a
function of `check_in_recovery` handed the bot and the queue, in the order they run:

1. **missed check-in calls** (`recover_missed_check_in_calls`): a call that fell due while the bot
   was down, its deadline ahead, is asked as late, in the order its rounds race;
2. **RSVP views and deadlines** (`recover_rsvp_views_and_deadlines`): the buttons of every standing
   call are armed again, and a deadline that passed while the bot was down is asked;
3. **missed check-in clean-ups** (`recover_missed_check_in_clean_ups`): a check-in due to come
   down a day after its round is asked to.

The calls come before the deadlines, as a call comes before its deadline, and the clean-ups after,
so a check-in is distributed before it goes. A recovery posts nothing: it asks the queue, which runs
what is asked once it starts, and every moment it judges is read at the queue's clock.

**What a league's rounds are judged by.** The call and the deadline are caught up for the rounds of
a season in an ongoing stage (`ONGOING_STAGES`), which core's `rounds_of_ongoing_seasons` reads;
the clean-up for every season but a cancelled one, as its timer is. A recovery reads core's tables
only through core's readers and its own tables, `attendance_config` and `rsvp_embed_messages`,
itself. Pinned by `test_no_late_call_for_a_round_left_alone` (a season pending completion is owed
no call), `test_a_round_a_day_past_has_both_its_cleanups_run` and
`test_a_cancelled_round_is_left_alone`.

**A call, a deadline or a clean-up asked both by the sweep and by its timer is done once.** The
change's key is `<kind>:<round_id>` and its check finds the work no longer due once it is done, so
whichever is asked second is dropped. Pinned by
`test_a_call_missed_by_minutes_asked_by_the_restart_and_its_timer_is_posted_once`,
`test_a_deadline_missed_asked_by_the_restart_and_its_timer_announces_once` and
`test_a_clean_up_missed_asked_by_the_restart_and_its_timer_takes_down_once`.

---

## Posting to Discord

Answers to `architecture.md`, "Posting to Discord". Every post, edit and delete attendance makes is
a job of a change, and goes through core's handler for its kind of post.

| Post | Kind | Channel | Handler | Id kept |
|---|---|---|---|---|
| the check-in call, its last notice and the distribution's announcement | notice | the division's check-in channel, read as it then stands | `send_notice`, naming who it may mention | the job's record, on `rsvp_embed_messages` |
| the call's redraw as answers come in | notice | the call's own | `edit_notice` | the call's own |
| the call's take-down | notice | the call's own | `delete_notice`, through the one delete step every standing post shares | the row, forgotten as it goes |
| the attendance sheet | standing post | the division's attendance channel | `post_standing`: the new one is up before the old is deleted | `attendance_division_config.attendance_message_id` |
| the cancellation notice | notice | the division's check-in channel | `post_cancellation_notice`, core's | not kept |
| the sanction cards and their banner | results' `review_verdicts` jobs | the division's verdicts channel | results' handlers | results' |

The call is a notice and not a standing post: it is edited in place as drivers answer, and its id is
kept to be edited and deleted again. A call that replaces one standing plans a `delete_earlier` job
for each of the earlier call's messages **after** the new call's, so the earlier call comes down
only once the new one has landed and is recorded. Pinned by
`test_a_call_replacing_an_earlier_one_deletes_its_messages_as_jobs_after_it` and
`test_a_repost_that_fails_leaves_the_earlier_call_standing_and_says_so`.

### Pictures and their fallbacks

The call and the sheet may carry a picture the image module draws. A call's or a
sheet's graphic is tried on the first try alone and text on every later one (Constitution XIV.8), so
a stop on a post Discord refused is retried as text.

A graphic that cannot be drawn is not a failure of the post: the call goes out without it and the
sheet as text, and **the fault is returned as a line of the job**, naming the division and what
was left out, so the league is told in the log channel, the full error going to the host's log. The
catch holds the drawing only, never the post (`architecture.md`, "A catch-all error handler", sixth
place), and it is in four functions: `_checkin_attachment` (the call's graphic),
`_sheet_attachment` (the sheet's graphic), `_round_grid` (the track registry the graphic draws its
flags from: a registry that cannot be read draws the headings without flags) and `_seat_team_field`
(the team names it draws: teams that cannot be read are drawn without names). The team names and
their shorthands are read from the same teams, so a fault reading them is told once. Pinned by
`test_a_call_whose_graphic_cannot_be_drawn_is_told_as_a_line_of_its_job` and
`test_a_sheet_whose_graphic_falls_back_is_told_as_a_line_of_its_job`.

**A sheet whose round grid cannot be read raises**, in place of drawing with an empty grid and
replacing the sheet standing: a grid that is empty because it could not be read looks like a league
with no rounds, and the standing sheet, which is right, would be deleted for it. The stop is the
queue's, retried when the database can be read. Pinned by
`test_a_sheet_whose_grid_cannot_be_read_stops_and_leaves_the_standing_one`.

### The sheet

A division's sheet is posted by one of three jobs, each raising where it cannot post:
`post_sheet`, which the sync plans among its jobs, and `opening_sheet` and `final_sheet`,
attendance's jobs in core's approval of a season's placements and in its completion, under the
occasion's heading. Each stops the queue where Discord refuses the post or the division has no
attendance channel, to be set and retried. The rows are the drivers core seats full-time in the
division (`seat_holders_on`), and every driver with a total recorded in the division up to the
round, so that one sacked or moved to Reserve stays on the sheet (`_sheet_rows`' docstring). The
opening sheet is drawn against the division's first round, the final sheet against the round the
completion hands it, and the sync's against the last round it recalculated.

---

## How modules and core fit together

Answers to `architecture.md`, "How modules and core fit together".

### The hooks attendance signs up to

Attendance declares no hook of its own. Core declares them and attendance subscribes in
`wiring.py`; where several modules join a hook the entry point calls the function in its order.

| Hook | Attendance's function | What attendance does |
|---|---|---|
| `starting` | `wiring.start_up_steps` | its three start-up steps (above) |
| `round_windows_read` | `attendance_hooks.round_window` | hands its three lead times, or none where the module is off |
| `review_sections_read` | `attendance_hooks.review_section` | its section of a season review |
| `round_amended`, `round_armed`, `round_clean_ups_due` | `check_in_changes.round_amended_on`, `amendment_posts`, `amendment_clean_ups` | reopens the check-in inside the amendment's save; posts the call again when the round is armed; hands the clean-ups due |
| `amendment_steps` | `check_in_changes.amendment_steps` | the steps an amendment asks of the check-in |
| `rounds_called_off`, `cancellation_notices`, `cancellation_steps` | `take_down_called_off_on`, `notices_on`, `take_down_steps`, `read_check_in_steps`, `notify_checkin_steps` | takes down the calls, reads the check-in's record before they come down and plans its own notice |
| `rounds_deleted`, `divisions_deleted`, `profiles_deleted`, `profiles_merged`, `bot_packed` | `attendance_hooks` | see "The database" |
| `division_lines_read`, `division_channel_faults_read`, `channel_uses_read`, `setting_channels_read` | `attendance_hooks`, `division_channels` | its channels, read for the reviews, the channel registry and the factory reset |
| `fingerprint_areas_read` | `fingerprint_areas.fingerprint_areas` | its channels and its configuration in the season's fingerprint, whatever its switch |
| `pending_steps_read`, `review_marks_read` | `test_mode_steps.pending_steps`, `review_marks` | a round's check-in for `/test-mode advance` and its review |
| `placements_confirmed`, `placements_confirmed_steps`, `season_completed`, `season_completed_steps` | `sheet_posting` | plans and defines the opening and the final sheet |

Attendance's switch is the keyed hook `ModuleService.register_switch` under `"attendance"`. The
module's own work checks the switch where it starts, and a subscriber that needs it reads it itself.

### What results asks of attendance

A round's review asks of attendance what results' protocol, `AttendanceAfterReview`
(`results/services/module/attendance_hook.py`), declares; attendance implements it
(`attendance_after_review`), and the builder hands it to results' change types. Its writers write on
the save they are handed and commit nothing, so a round's attendance lands with the penalties and
points or not at all; its posting methods raise `StepFailedOnDiscord` for the queue to stop on.
Which methods act whatever the switch says is the protocol's docstring's. **A sanction is one
driver's:** the candidate carries the profile, the Discord user, the sanction and the divisions an
autosack reaches, and the thresholds and the driver's total are read again when the sanction is
applied or announced. It is checked (`check_sanction`, with an autoreserve's capacity guards), saved
on the caller's save (`apply_sanction_on`, through core's `move_on` and `sack_on`, which stamp the
queue's clock) and has its roles changed (`change_sanction_roles`) in three calls.

### What attendance reads of others

Attendance reads core's and results' tables only through their owners' readers (`architecture.md`,
"Each table is read and written by its owner alone"), among them: a round, its division and season
from core's round facts (`round_facts_on`), the drivers a division seats from `seat_holders_on`, a
division's teams (`division_teams_on`), a driver by Discord account and a division's placements from
core, drivers' test-mode names (`driver_test_names_on`), the order of a division's rounds
(`division_round_states_on`, `division_rounds_in_order_on`), the track registry (`tracks_by_name`),
the rounds of a season in an ongoing stage from `rounds_of_ongoing_seasons`; and from results, who
raced a round (`profiles_with_a_result_in_round_on`), a division's verdicts channel
(`verdicts_channels_on`), the constructors' positions going into a round
(`team_positions_in_round_on`) and an amendment open in a division (`open_amendment_in_division`). A
reader answers as a join on the tables would, so that reading through it changes nobody a league
calls, scores or sanctions: a check-in press is judged by the seat holders (`seat_holders_on`)
filtered by the placements (`placed_drivers_on`), so that a driver holding a seat in the division
with no placement row there is refused as not a member
(`test_the_seat_read_through_core_judges_a_driver_as_the_old_join_did`,
`test_both_seat_joins_read_the_drivers_core_s_reader_seats`).

### The dependency table and the image module

Attendance needs results (`NEEDS`, `core.md`, "The dependency table"): the sanctions and the
recalculation both stand on a round's results. It may ask the image module to draw its posts, and
uses no other module (the `attendance-uses-no-other-module-but-results-and-image` contract). The
image module draws the call (`try_attach`) and the sheet (`image_attendance_post`) from what
attendance hands it, and reads the deadline from `derive_checkin_deadline`.

---

## Errors and failures

Answers to `architecture.md`, "Errors and failures".

| Starting point | Attendance's part |
|---|---|
| A command, button or form | the base classes and `report_failure`; the cog catches by name only what it turns into a refusal. A button id the bot cannot read is a fault of the bot's, raised and reported through `report_failure`; an action it does not know is a refusal (`test_an_unparseable_check_in_button_id_is_a_fault_not_a_refusal`) |
| A change | its check refuses a member's request and drops the bot's where the work is no longer due; a job that fails stops its lane, with the stop notice, Retry and Discard |
| A timed job | the scheduler's job runner: a timer's callback asks its change, and one that raises is reported there |
| A start-up step | `RecoveryFailed`, below |
| A picture | the fallback above: the fault returned as a line of the job |

**A recovery that fails on one round stops none of the others.** `check_in_recovery` goes through
every round, catching and noting the failure of each, and raises `RecoveryFailed` once at the end,
naming each round and its error, which the start-up sweep reports to the log channel in one line.
A database it cannot read is raised as it is, for the sweep to report: nothing is caught up, and
nothing says so but that line. Pinned by
`test_a_call_that_cannot_be_asked_is_reported_once_and_the_next_round_s_still_is` and
`test_a_clean_up_that_cannot_be_asked_is_reported_once_and_the_next_round_s_still_is`.

**What a stop leaves** is `architecture.md`'s ("Errors and failures"). The sync's roles are the one
exception: they end on a fault, the sanction stands, and the fault goes to the host's log (the
specification's "Resynchronising attendance"). The call's refusal says what is left for the round
(`left_for_the_queue`).

**The catch-alls** of attendance sit in the places `architecture.md` allows, and each is named for
good in `CATCH_ALLS_ALLOWED` where the shape does not tell its place: `_record_failed`, around
recording a failure that might itself fail (place 3); the four drawings of a graphic above (place
6); the loop of each recovery, one round failing never stopping the rest (place 2); and the
advance's ask of a change, which raises the fault again as `AdvanceStepFailed` for the command to
report, a handler in a place by its shape.

**What a restart does to interrupted work** (the core specification's "When the bot stops"):

- **a change cut off** is finished by the queue from its first job not done, so a sync that saved
  its sanctions finishes their cards, each announced once;
- **a call, a last notice or a deadline that fell due** is caught up, as above;
- **the buttons of a standing call** are armed again for every row;
- **a check-in due to come down** is taken down.

---

## Constraints a later reader might tune away

Each is stated where it lives and pinned by the test named.

| Constraint | Stated in | Pinned by |
|---|---|---|
| The switch-on writes the flag alone and leaves the eight values to the column defaults | `module_on_attendance`'s save | `test_re_enabling_attendance_starts_from_the_packaged_defaults` |
| A read of a round's window creates no row | `attendance_hooks.round_window` | `test_reading_a_round_s_window_writes_nothing` |
| A cross-setting rule is judged by the setting's change as it starts | `attendance_settings`' docstring | `test_set_setting_refuses_a_timing_that_breaks_the_invariant` and its two companions |
| A stored copy has one recompute, run on its caller's connection | `_recalculate_forward`'s docstring | the three tests of "The stored copies of derived figures" |
| The sync's recalculation and sanctions are one save, its cards later jobs | `attendance_sync_change`'s docstring | `test_a_stop_between_the_sanctions_and_their_cards_finishes_on_restart` |
| A sanction the sync cannot apply never stops the others | `attendance_sync_change`'s docstring | `test_a_sanction_the_sync_cannot_apply_never_stops_the_others` |
| A refused role change leaves the sync's sack applied and the queue running | `attendance_sync_change`'s docstring | `test_a_role_change_discord_refuses_leaves_the_sync_s_sack_applied_and_the_queue_running` |
| An answer is judged at the press, not as its turn comes | `check_in_answer_changes`' docstring | `test_an_answer_pressed_before_the_lock_counts_though_it_runs_after` |
| The earlier call comes down only after the new one is posted and recorded | `check_in_changes`' docstring | `test_a_call_replacing_an_earlier_one_deletes_its_messages_as_jobs_after_it` |
| A round is owed one call, however many are asked at once | `check_in_call_change`'s docstring | `test_two_calls_posted_at_once_for_one_round_post_one` |
| `distribute` and `mark_cleared` cannot be discarded | `would_never_be_placed` and `would_lose_the_answers`, the readers of what a drop would leave | `test_placing_the_reserves_cannot_be_discarded`, `test_marking_the_check_in_over_cannot_be_discarded` |
| The four timers are armed with no lateness limit, and the handlers judge lateness | `schedule_attendance_round`'s docstring | `test_a_check_in_timer_is_armed_with_no_lateness_limit` |
| A missed last notice is sent late, while the deadline is ahead | `_notice_not_due`'s docstring | `test_a_last_notice_missed_while_the_bot_was_down_is_sent_when_it_is_back` |
| A last notice asked before its late call posts waits behind the call | `_notice_not_due`'s docstring | `test_a_last_notice_asked_before_the_late_call_posts_is_posted_once_the_call_stands` |
| Under test mode lateness is judged at the ask only | `_left_to_advance`'s docstring | `test_a_timer_fired_on_time_under_test_mode_is_posted_however_late_the_queue_starts_it` |
| The recoveries judge a season by the stages the call, the deadline and the clean-up are each owed in | `check_in_recovery`'s docstring | `test_no_late_call_for_a_round_left_alone`, `test_a_round_a_day_past_has_both_its_cleanups_run` |
| A failed recovery stops none of the other rounds and is reported once | `check_in_recovery`'s docstring | `test_a_call_that_cannot_be_asked_is_reported_once_and_the_next_round_s_still_is` |
| Calls are recovered before deadlines, and clean-ups after both | the comments in `wiring.start_up_steps` | `test_the_builder_orders_the_start_up_steps` |
| A button id the bot cannot read is a fault, not a refusal | `handle_rsvp_button`'s docstring | `test_an_unparseable_check_in_button_id_is_a_fault_not_a_refusal` |
| A sheet whose round grid cannot be read leaves the standing one | `_round_grid`'s docstring | `test_a_sheet_whose_grid_cannot_be_read_stops_and_leaves_the_standing_one` |
| A graphic that cannot be drawn is told as a line of its job | `_sheet_attachment`'s and `_checkin_attachment`'s docstrings | `test_a_sheet_whose_graphic_falls_back_is_told_as_a_line_of_its_job`, `test_a_call_whose_graphic_cannot_be_drawn_is_told_as_a_line_of_its_job` |
| A seat holder with no placement in the division is refused as not a member | `check_in_answer_changes._facts`' docstring | `test_the_seat_read_through_core_judges_a_driver_as_the_old_join_did` |
