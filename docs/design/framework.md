# The framework — why the code is shaped as it is

This file says how the framework's code is laid out and why: the machinery every part of the bot
runs on, which knows no league rule. It covers its folders and services, the change queue and the
settings it builds, the database code, the scheduler and the start-up sweep, the posting handlers
and the log writer, the hook types and the module service, and the failure paths. Every path is
under `src/leaguebot/` unless it says otherwise.

It cites rules rather than restating them. What applies to every part of the bot is
`architecture.md`'s, cited by its section heading; this file says only how the framework's code
carries it out. What core does with the machinery, its own settings, timers, posts and start-up
steps, is `core.md`'s, and each module's is its own design file's. A constraint on one function
lives in that function's docstring, pinned by its test, and is named here only where the shape
depends on it. Where this file and the code disagree about what the code does, the code wins, and
the file is corrected at close-out.

---

## How the code is laid out

Answers to `architecture.md`, "How the code is laid out", which sets the three layers: the
framework imports `utils` and nothing of core or any module. The framework's code sits in
`framework/cogs/`, `framework/db/`, `framework/models/`, `framework/services/` and
`framework/utils/`.

### Services, and what each owns

The framework's services sit in four topic folders of `framework/services/`. The table names a
service by its file, not its folder.

| Folder | Services | Owns |
|---|---|---|
| `queue/` | `change_queue` (`ChangeQueue`), `change_types`, `queue_records`, `setting_change`, `queue_stop_view`, `typed_turns`, `audit_service`, `authority` | `queued_changes`, `queued_change_steps`; `audit_entries` (`audit_service`, the record of what changed) |
| `jobs/` | `scheduler` (`SchedulerService`), `start_up` | the job store, a file of its own |
| `posting/` | `output_router` (`OutputRouter`), `notice_handler`, `standing_post_handler`, `owned_channel_handler`, `standing_posts`, `retry_service`, `channel_tell_change`, `league_settings` | `pending_messages` |
| `switches/` | `module_service` (`ModuleService`) | nothing: each switch is read through the reader its module signs up |

**What the framework asks of the league it does not decide.** `authority` declares the authority
tiers the change queue asks (who is a league admin, who a league manager) and `league_settings` the
league's settings the posting handlers read (the claimed server, the roles and the log channel),
each as a protocol. Core's `ConfigService` fulfils both, so the rule stays in core
(`core.md`, "Services, and what each owns") and no framework service names a core type; only
`LeagueBot` does, for the type checker (`architecture.md`, "How the code is laid out").

**The framework's models** (`framework/models/`) are plain data: the change queue's vocabulary
(`change.py`), the refusal a member reads (`Refused`, `refusal.py`), what a start-up step's error
carries for its rounds (`RoundsFailed`, `recovery.py`), the league's settings row as the framework
reads it (`league_settings.py`) and a log line waiting to be delivered (`pending_message.py`).

**The framework's helpers** (`framework/utils/`) are the bot's type (`league_bot.py`), the
one-league claim and the base classes of the command tree, views and modals (`league_server.py`),
the hook types (`hooks.py`), the failure paths (`interaction_errors.py`, `answering.py`, the job
runner `job_runner.py`), the helper that starts background work and holds it (`background.py`), the
standard log lines, the words a reply uses to link to a logged line and to list the notices of the
pictures its work drew, and the frame a job puts round those notices (`notices_block`,
`job_notice_lines`, `framed_picture_lines` and its `PictureLines`) (`log_lines.py`), a member as the
log channel names them (`member_names.py`), the log filters, `bounded_autocomplete`
(`autocomplete.py`), the guard that refuses a post made outside a job (`in_a_job.py`), the wait for
the catch-up after a restart (`catch_up_gate.py`), the channel cast (`channels.py`) and the path
resolution that keeps a path inside the project (`paths.py`).

**The retry loop's cog** (`framework/cogs/retry_cog.py`) holds no command.

**`LeagueBot`** (`framework/utils/league_bot.py`) declares every attribute the builder attaches,
each hook among them, and `test_every_attribute_bot_py_attaches_is_declared` holds the two equal.
The builder, which joins the framework, core and the modules, is the entry point's (`core.md`, "The
entry point").

---

## How modules and core fit together

Answers to `architecture.md`, "How modules and core fit together".

### The hook types

`framework/utils/hooks.py` declares `Hook`, whose subscribers are plain functions, and
`AwaitedHook`, whose subscribers are awaited one after another; each `call` returns what each
subscriber handed back, in the order they signed up. A subscriber that raises is not caught: the
caller's own failure path has it (the module docstring;
`test_a_hook_calls_its_subscribers_in_order_on_the_handed_connection`). The builder makes every
hook, signs the modules up and attaches it to the bot; a cog reads it off the bot and hands it on,
and a change type is handed it by the builder. Which hooks core declares, and who signs up to each,
is `core.md`'s ("The hooks core declares").

`StartingHook` is of another shape: it holds core's own steps, signed up by name, and each module's,
signed up as one function bound to the bot, under the module's switch key. The hook calls nothing:
`start_up` reads it and runs each step contained, a module's only where its switch is on, save a
step the module marks `runs_while_off` (see "Start-up"). `StartUpStep`, a name and what it runs, is
declared beside it.

### The module service

`ModuleService` (`framework/services/switches/module_service.py`) is a keyed hook: each module's
wiring signs up the reader of its own switch (`register_switch`), under its switch key, and the
service asks it by the key alone (`is_enabled`, or `is_enabled_on` on a connection the caller
holds). It reads no module's table or column and names no module
(`test_the_module_service_names_no_module`). A key nobody signed up is a fault in the bot's
assembly, `SwitchNotSignedUp`, never read as off
(`test_a_switch_no_module_signed_up_for_is_an_error_not_off`). Who asks for which switch, and by
what name, is `architecture.md`'s ("How modules and core fit together").

---

## How a change is carried out

Answers to `architecture.md`, "How a change is carried out", which designs the queue; the core
specification's section of the same name holds what a league is told, and `core.md`'s the change
types core registers.

### The queue's code

The queue is `ChangeQueue` (`framework/services/queue/change_queue.py`), and it is one class:
asking, the dispatcher, a change's start and end, a step's run, the stop and its notice, Retry and
Discard, the audit and the held reply are one state machine around one lock order (`_working[lane]`,
then `_asking`, then `_writing`) and the attributes they share. *Rejected:* mixins, which would
split the file and leave one state machine whose lock order no single class states; and collaborator
objects, which would reach into the queue's private state or be handed all of it (`ChangeQueue`'s
docstring). What reads none of that state is out of the file.
`framework/services/queue/change_types.py` holds the types a change is made of, `Step` and
`ChangeType` with the contexts a check, a step, an outcome and the refusal and discard hooks are
handed, `StepView` among them, and `drawn_notices_of` and `drawn_notices_in`, which gather the
notices of the pictures a change's jobs drew, of the jobs of one name and of every job, for its
outcome, and the decisions that govern them (a post's id saved with its mark, a step's four kinds, a
job that ends on a fault); `framework/services/queue/queue_records.py` holds the bookkeeping over
the queue's tables, what is in hand, what was ever asked, the job numbering and the emptying of a
restored database, which a check, a recovery, a restore and a factory reset call without a queue.
The queue file keeps the constants the class reads: `RETRY_AFTER`, the bot's own tries;
`CONVERSATION_LANES_AT_ONCE`; `UPDATABLE_FOR`, how long a reply may be updated;
`WORKER_PAUSE_AFTER_FAULT`; and `NOTICE_REPOST_EVERY`; with `_confine` and `QUEUE_TABLES`. The plain
data is in `framework/models/change.py`: `StepKind` (`SAVE`, `ACT`, `DELETE`, `EDIT`), `Verdict`,
`StepResult`, `DrawnNotices` (one picture's notices, as a reply lists them), `NoticeBlock` (a
picture's notices marked as such, so the job that drew it can open them), `DrawnKept` and
`drawn_kept` (what a post job's earlier tries kept on their stops), `StepFailedOnDiscord`,
`GuildUnavailable`, the lane names and the keys a job that drew puts on its result
(`RESULT_NOTICES`, `RESULT_DRAWN`, `RESULT_PICTURES`, `DRAWN_LINES`, `DRAWN_NOTICES`,
`DRAWN_LINKED`, `DRAWN_PICTURES`). The engineering decisions that govern the queue are in its module
docstring, and those that govern a step's kinds, its record and a fault's ending in `change_types`',
each pinned by a test in
`tests/framework/test_change_queue.py`, `test_change_queue_lanes.py` or
`test_change_queue_controls.py`. Among them:

- **One save at a time across lanes**, under an in-process lock (`ChangeQueue._writing`) held for
  the save alone, with a fixed lock order. *Rejected:* one worker for every lane, which holds a
  conversation behind the league's Discord calls; and SQLite's own busy wait, which gives up where
  a save is held longer (the module docstring; `test_two_lanes_saves_never_overlap`).
- **A post's id is saved with its mark** (`Step.record`). *Rejected:* a `SAVE` job after each post
  to write its id, which doubles the jobs a change numbers and stops on (`change_types`' docstring).
- **A change starts only if it is still queued:** the claim that sets it running is conditioned
  on its state in the statement, so a change ended while its check ran does not start
  (`test_a_change_discarded_while_its_check_runs_does_not_start`).
- **The queue is held from the entry point's `hold()` until the start-up sweep has asked its own**
  (`sweeping()`, whose leaving, or `release()`, lets the asks go ahead; releasing twice is
  harmless). Every `ask` and `ask_after` made outside the sweep waits before its check, so nothing a
  member does or Discord reports lands on a lane ahead of a recovery asked a moment later. An
  interaction not yet answered is deferred, privately, before the ask waits, for Discord's three
  seconds. A kind that declares a `turn_channel` puts it in `_held_turns` while its ask waits, which
  `held_turn_channels()` returns, so `turn_waiting` counts a typed answer held there as waiting and
  a second message typed meanwhile is turned away. *Rejected:* the held channels in `_held`, which
  holds the kept replies. A queue never held asks at once, as every test queue does. A command
  answered off the queue waits through `wait_out_the_hold` by the `after_the_catch_up` decorator
  (`framework/utils/catch_up_gate.py`), worn below the tier guard, which hands the command a
  response that follows up in the place of the one the deferral used; each cog gives it the way to
  its own queue, a helper never reading the queue off the bot.
- **A kept reply is the same entry held in memory**, let go once it can no longer be updated.
  *Rejected:* a second table of replies (`ChangeQueue.keep_reply`'s docstring;
  `test_a_kept_reply_no_later_change_takes_over_is_let_go_once_it_cannot_be_updated`).
- **A reply links to the log line its job named, from the queue's memory.** A job that drew a
  picture names the one line of its result that holds the picture's notices (`StepResult.linked`);
  once the job's save has committed and its lines are delivered (`OutputRouter.deliver_queued`
  returns each delivered line's link), the queue keeps the link for the job on the change's held
  reply (`_Held`) and hands it to the outcome as `StepView.log_link`, so the log message is written
  before the reply that points at it. A restart loses it with the reply it feeds, and a log channel
  that refuses costs the link alone. An outcome lists the notices through `drawn_notices_of` or
  `drawn_notices_in` and `drawn_notices_section`, so every reply words them alike, in one section,
  "Image notices", whichever change's reply it ends (the image specification's "Errors and the
  rejection of input"). *Rejected:* saving the link with the job,   a column for a reply that cannot
  outlive the restart   (`test_an_outcome_links_to_the_line_its_job_named`).
- **A notice block is opened with the member and the change by the job that drew the picture, and
  image marks which lines they are.** A drawing knows no member and no change, so image hands its
  notices back grouped by kind and never opened. A post aspect's drawing (results, attendance and
  weather) comes back headed, as a `NoticeBlock` (`ImageRenderService.format_notices`), and the job
  that posted it opens each block through `framed_picture_lines`, with `named | what |`, and joins
  its blocks in one line, so that they reach the log in one message and the reply links to it once.
  What `ImageDrawing` draws for core (the calendar, the lineups and a review's pictures) comes back
  as the grouped lines alone (`grouped_notice_lines`), with no heading and no mark, which core's
  jobs head and open in one go through `drawn_notices_line`, as does the lineup a command draws; a
  preview's runner opens its own message (`preview_runner._log_notices`). The framing is done before
  `StepFailedOnDiscord.carrying` keeps the lines, because a `str` subclass does not survive being
  saved in a result; a job that drew several pictures keeps each one's label and notices
  (`RESULT_PICTURES`, `DRAWN_PICTURES`), so a stop loses neither which picture a notice belongs to
  nor the block's opening
  (`test_an_outcome_gathers_every_picture_s_notices_once_a_stopped_job_kept_each_label`).

The stop notice's buttons are one persistent view, `QueueStopView`, registered as the queue
starts. A message typed into a conversation's channel is answered through `typed_turns`. A line
told to a channel as a change of its own is `channel.tell` (`channel_tell_change`).

### Settings

Every settings command, core's and each module's, declares a `Setting`, and `setting_change`
builds its change type, so the line, the audit entry, the answer for a value already held and the
answer for a fault are formed in one place. `setting_change`'s docstring holds the shape and its
rejected alternatives, each pinned by a test in `tests/framework/test_setting_change.py`. Which
settings overtake a stop is `Setting.repairs_the_bot` (`architecture.md`, "A job that fails stops
its lane"), set by each setting's declaration; a step that carries the save's line writes it only
once it has succeeded (`carries_line`). Core's settings are listed in `core.md`, "A league's
settings".

A setting may run steps before its save (`Setting.before`, each a `Before`), for work the save waits
on, such as a module's kept channels checked before it is switched on. Each is a step of the
change, so a restart resumes at it. A fault in one ends the setting as a fault in its save does,
with nothing saved and nothing audited. A step may end the setting refused by returning a `HALTED`
result carrying the reply and the reason, from which `setting_change` forms the one ⛔ line, the step
writing none. What the steps found is handed to the save under `FOUND` in its payload, read from
the steps the queue keeps rather than from memory, so a restart between a step and the save
hands it the same. The docstring of `setting_change` holds why the steps before the save keep the
queue's last-judgement invariant.

A step that changes something on Discord declares an undo (`Before.undo`), itself an `ACT` step.
The undos of the steps that returned run, last step first, where a later step halts or faults and
where the save does, planned as jobs of the change in the place of the rest, so a restart finishes
them; a step that fails part-way takes back its own partial work before it raises, and its undo is
not run. The pattern a module follows is a step that records what is there, a step that changes it
whose undo restores the record, and the save, the record being the record step's result among the
steps the queue keeps (`StepContext.steps`) rather than in memory. Where Discord refuses a
take-back, whether the undo's or a step's own part-way one, the step raises `StepFailedOnDiscord`
with a notice of what was left and how to put it right; `setting_change` carries the notice into
the failure line and the reply, and names the kind of fault from its cause, or from its reason
where it chained none, never `StepFailedOnDiscord`.

### The clock

The queue's clock, `ChangeQueue.now`, is the bot's: the builder hands services, change types and
the log writer `now=lambda: bot.change_queue.now()`. In the running bot it is the wall clock in UTC;
a test hands the queue a clock of its own.

---

## The database

Answers to `architecture.md`, "The database". The database code is `framework/db/database.py`:
`get_connection`, which lends idle connections again, `close_connections`, `run_migrations`, and
the helpers `sole_row` and `inserted_id`. The schema's one baseline is
`framework/db/migrations/001_baseline.sql`; `run_migrations`' docstring says how a migration is
applied and when one is refused.

The framework owns four tables (`TABLE_OWNER` in `tests/repository/test_architecture_rules.py`).
Core reads and writes them only through the framework's services (`queue_records`, `audit_service`,
`retry_service`), as `architecture.md`'s rules on table owners have it; how it does so is
`core.md`'s ("Core's tables"). `schema_migrations` is the migrations' own record, written by
`run_migrations` alone.

| Table | One row is | Held to it by |
|---|---|---|
| `queued_changes` | one change asked of the queue | CHECKs on `origin` and `state`; `queued_changes_one_running` |
| `queued_change_steps` | one job: a step of a change | `UNIQUE (change_id, position)` |
| `audit_entries` | one recorded change | — |
| `pending_messages` | one log line the log channel has not yet taken | — |

| Constraint | Stated in | Pinned by |
|---|---|---|
| SQLite's busy timeout is five seconds, sized for the longest legitimate write; an autocomplete asks for a shorter one (`AUTOCOMPLETE_TIMEOUT_SECONDS`) | `framework/db/database.py`, `_BUSY_TIMEOUT_SECONDS` | `test_connections_carry_an_explicit_busy_timeout` |
| The database runs in WAL with `synchronous` left at FULL, durability chosen over speed | `_enable_wal` | `test_migrations_leave_the_database_in_wal_mode`, `test_connections_keep_full_durability` |
| Four idle connections are kept to be lent again | `_IDLE_CONNECTIONS_KEPT` | `test_no_more_than_four_idle_connections_are_kept` |

---

## Timed work and restarts

Answers to `architecture.md`, "Timed work and restarts". What each kind of job does when missed is
the core specification's ("When the bot stops"); a round's timers are armed by core's `RoundJobs`
(`core.md`, "Core's round jobs").

### The scheduler

`SchedulerService` (`framework/services/jobs/scheduler.py`) is the one place a job is armed, found
or removed, and the only file that imports APScheduler. It names no round, module or kind of job:
it arms a job at the id, kind and moment it is handed, through its generic calls (`arm_at`,
`run_now`, `arm_daily`, `arm_every`, `jobs` and `remove`). **Its job store is a SQLite file of its
own**, apart from the league's database, for the reason its module docstring gives: the store
writes synchronously on the event loop's thread.

Every job runs through one module-level callable, `_one_off_timer_job`, which looks up the handler
signed up for the job's kind (`register_timer`, a keyed hook) and hands it the job's key: a round's
id, a one-off timer's key, or nothing for a standing job. **It runs the handler through the job
runner** (`run_job`, named for the kind and the key), handing it the service's `report`, the log
channel's `post_log`, which the builder gives the service and which the service holds for its life,
so that a handler that fails before it asks for its change is reported by one line. Two things
share the name of a runner here, and are kept apart: `_one_off_timer_job` is the callable the job
store pins, and the job runner is what it calls. A kind with no handler is passed over with a
warning, and so is a service not yet started: a fault in the bot's assembly, not in a job. **A job
runs in an empty context** (`_JobScheduler`), so it can never take an interaction for its own
(`test_a_job_scheduled_from_an_admitted_interaction_finds_no_member_to_warn`).
A one-off timer is armed by `arm_once` (`architecture.md`, "Timed work and restarts").

### Start-up

When Discord first reports the bot ready, `start_up` (`framework/services/jobs/start_up.py`) runs
once per process, as the sweep of everything that came due while the bot was stopped. What runs
before it, from reading the settings to connecting, is the entry point's (`core.md`, "Start-up").

- **It starts the scheduler paused**, so the job store can be read while no job runs, and resumes
  it last, in a `finally`, so a queue that fails to start does not hold the jobs paused.
- **It runs core's own steps, then turns to each module in turn.** Inside the queue's `sweeping()`
  it runs core's steps in the order they signed up, one at a time. Then, for each module in sign-up
  order, it reads the module's switch (`ModuleService.is_enabled`, by the switch key), passes the
  module over, with a line in the host's log, where it is off, save for any step the module marks
  `runs_while_off`, which runs whatever the switch says, and otherwise runs the module's own steps
  in the module's own order (`architecture.md`, "Timed work and restarts", says when a step may be
  so marked). Each enabled module catches up its own missed events in its turn: no pass
  interleaves the events of different modules by time (`architecture.md`, "Timed work and
  restarts").
- **Each step, each switch read, each module's list of steps and the queue's start is contained**:
  one that raises is logged with its traceback and reported to the log channel by one line
  (`start_up_failure_line`), and the rest carry on, the module's later steps included. Where the
  error carries `rounds_called` (the protocol `RoundsFailed`, `framework/models/recovery.py`: each
  round a step failed for, named as core names it), the line names them, and never asks for a
  module's own error type, so each module's `RecoveryFailed` is its own; an error carrying none is a
  step that failed as a whole, and its line names no round. A module whose switch or list cannot be
  read is passed over; a switch not signed up is a fault in the assembly and is reported, never read
  as off.
- **The hold is released when the sweep has asked its own**, on leaving `sweeping()`, and again in
  an outer `finally`, so a scheduler that cannot start does not leave every request waiting for the
  rest of the run.
- **The queue's start follows the sweep.** `ChangeQueue.start` registers the stop notice's view,
  leaves every stopped job stopped, delivers lines never tried, posts any stop notice not
  saved, and starts the dispatcher.
- **A guard of its own** makes a second ready, after a network drop, run nothing.

Each is pinned by a test in `tests/framework/test_start_up.py`; `ChangeQueue.start`'s docstring
says what the queue's start does. Which steps are signed up, and in what order, is `core.md`'s
("Start-up").

| Constraint | Stated in | Pinned by |
|---|---|---|
| The scheduler starts paused and is resumed last, so a start-up step's work is asked before any timer that would act on the same thing can fire | `framework/services/jobs/start_up.py` | `test_no_job_runs_until_start_up_has_ended` |
| The queue is held from before the bot connects until the sweep has asked its own, and released whatever the sweep does | `ChangeQueue.hold`, `release` and `sweeping`, `start_up`'s docstring | `test_a_request_during_the_sweep_waits_until_the_sweep_has_asked_its_own`, `test_the_sweep_releases_held_requests_before_the_queue_starts` |
| A held typed answer counts as waiting, so a second is turned away | `turn_waiting`, `ChangeQueue.held_turn_channels` | `test_a_second_message_typed_while_the_first_is_held_is_turned_away` |
| The module path of the callable every job runs is pinned (`_one_off_timer_job`), since the job store keeps it | `framework/services/jobs/scheduler.py`'s docstring | `test_every_stored_job_names_the_runner_by_its_pinned_module_path` |

The retry loop for log lines is no step (`architecture.md`, "Start-up runs once, in one
function"): it is `RetryCog.retry_loop` (`framework/cogs/retry_cog.py`), started by
`LeagueBot.setup_hook` (`test_login_starts_the_retry_loop_once`).

---

## Posting to Discord

Answers to `architecture.md`, "Posting to Discord", which lays out the four handlers and their
files (`notice_handler`, `standing_post_handler`, `owned_channel_handler` and the log writer,
`OutputRouter`). What core posts through them is `core.md`'s ("Posting to Discord").

The framework posts two things of its own:

| Post | Kind | Channel | Posted by | Found again by |
|---|---|---|---|---|
| A log line | log line | the log channel the league's settings name (`LeagueSettings`) | `OutputRouter.post_log`, or `queue_log_on` in a step's save, delivered with the link of each line handed back | `pending_messages`, until delivered |
| The stop notice | log line with buttons | the log channel | `ChangeQueue._post_notice` → `OutputRouter.post_notice` | `queued_change_steps.notice_message_id`, or the queue's memory until that save lands (lost at a restart) |

A replaced standing post is deleted by a job after the new one, built by `standing_posts`
(`replaced`, `delete_replaced_step`), which also removes a copy a failed `record` left
(`remove_kept`). Its docstring holds the shape.

As `architecture.md` ("Posting to Discord", "Every post keeps three things") has it, the posts
named here are looked for in the channel they were posted in. The calendar, the lineup and the
attendance sheet keep that channel beside the id (`*_message_channel_id`), their post jobs hand it
back as `old_channel_id` and `replaced` plans the delete in `posted_in` of it. Weather's forecast
(`forecast_messages.channel_id`), a review's sheet and lineup, a results table, a round's standings,
a classification, the Sign Up button and the closed notice call `posted_in` themselves. In results
a replaced table or standing goes through `_deletes` from `PostedTable.old_channel_id`, while the
switch-off's erase (the purge), a resubmission's withdrawals and the cancelled change's old tables
call `posted_in` on the recorded channel directly; signup passes it straight into the payload of
its delete. Where none was recorded the delete falls back to the present channel, and no constraint
pairs an id with its channel; `posted_in`'s docstring holds the reasons, and
`test_replaced_plans_one_delete_of_the_old_message_in_the_channel_it_was_posted_in` pins the
fallback.

**The log writer contains its own faults**, as `architecture.md` ("Posting to Discord") has it:
in `OutputRouter._read_config`, `_try_send` and `_enqueue_if_configured`
(`test_a_database_fault_reading_the_log_channel_is_contained`). `log_destination` alone raises,
being a query rather than a line posted (its docstring). A line the log channel failed to take
waits in `pending_messages` until the retry loop delivers it (`retry_service`).

**A log line's mentions are wrapped by the router**
(`test_every_mention_in_a_log_line_is_written_as_code`). The stop notice's mention is decided by
`ChangeQueue._admins_to_notify`.

**The framework's posts off the queue** are `architecture.md`'s exceptions ("Every post is made in a
job"), in `OutputRouter`, `ChangeQueue._update_reply` and `typed_turns`. A handler's post made
outside a job is refused (`posts_in_a_job`, raising `PostedOutsideAJob`,
`framework/utils/in_a_job.py`).

---

## Errors and failures

Answers to `architecture.md`, "Errors and failures", which names each failure path and every
stated exception; each docstring there named carries its reason.

| Starting point | The code |
|---|---|
| A command, button or form | `LeagueCommandTree.on_error`, `LeagueView.on_error`, `LeagueModal.on_error` (`framework/utils/league_server.py`), each calling `report_failure` (`framework/utils/interaction_errors.py`) |
| A Discord event | `on_event_error` (`framework/utils/league_server.py`), attached by the builder with `setattr` |
| An autocomplete | `bounded_autocomplete` (`framework/utils/autocomplete.py`) |
| A change | `ChangeQueue._start` and `_run_step`, leading to `_stop`, or to `on_fault` or `on_check_fault` where the type gives one |
| A scheduled job | `_one_off_timer_job` (`framework/services/jobs/scheduler.py`), running the handler through `run_job` (`framework/utils/job_runner.py`), which logs the fault on the host and reports it to the log channel by `job_failure_line` |
| A start-up step, a module's switch read, a module's list of steps, the queue's start | `start_up`, each run through `run_job` and reported to the log channel by `start_up_failure_line` |
| The retry loop | `RetryCog.retry_loop`, running each cycle through `run_job` with a report that never queues, and `retry_service.attempt_delivery` |
| A background task | `start_background` (`framework/utils/background.py`), running the work through `run_job`, with a report unless the log channel is what failed or a line of the work's own closes it |

**The job runner never raises**: a fault in its report is logged on the host and dropped, since
the fault may be the one the report would write to, and a cancellation is no failure and passes
through (`test_a_report_that_raises_is_logged_and_the_runner_still_does_not_raise`). A caller gives
it no report where the work's own failure is what the log channel just refused (the router's member
warnings, the retry worker's own line), or where a line of its own closes the work (the factory
reset's clean-up). `start_background` holds each task in a set of its own until it ends, for the
reason `architecture.md` gives ("Background work is started through one helper"), and a task
cancelled is neither logged nor reported (`test_a_started_task_is_held_until_it_ends`,
`test_a_cancelled_task_is_neither_logged_nor_reported`).

**The retry loop guards each kept line in its own try**, as the core specification's "When the bot
stops" requires of a line that faults, so that one line's database fault neither stops the lines
after it nor the loop (`test_one_entry_that_raises_does_not_stop_the_rest_or_the_loop`). A cycle's
fault, whole or for some lines, goes through `run_job` with a report that posts through
`post_own_line` and never queues; the ids of the lines that faulted stay in the host's log. The
catches of `attempt_delivery` and of `_post_own_line` (which `post_own_line` calls) are place 3's
(`architecture.md`, "Errors and failures"): a line that cannot be delivered stays kept and counted
for the next try, and the worker's own line falls back to the host's log.

**A failure line names the kind of fault, never the exception.** `describe_fault`
(`framework/utils/interaction_errors.py`) words the kind in plain words: that the bot could not read
or write its database, that Discord refused or failed a request, or that the bot hit an internal
fault. `failure_line`, `event_failure_line`, `job_failure_line` and `start_up_failure_line` each
print it in place of the exception, so that a command's, a button's, a form's, a setting's, a
change's, an event's, a timed job's and a start-up step's lines read alike and no Python class
reaches the log channel (core specification, "When a command fails"); the detail is the host's log's
(`test_a_job_failure_line_names_the_job_and_the_kind_of_fault`). It does not follow a cause, so an
error that gathers several faults reads as an internal fault. `BOT_FAULT` sits beside it: the words
a line that sends a manager to the host uses for a fault in the bot, which a picture's remedy reuses
(core's `picture_remedy`; the framework holds no rule of what a picture's fault asks, and so holds
neither the converter's absence nor the sources).

`report_failure` never raises: its reply and its line are each tried on their own. Its docstring
gives the reason for the reply's words. The tree steps aside for a command with its own error
handler (`test_a_command_with_its_own_error_handler_is_left_alone`). `on_event_error` drops the
listener's own arguments, which can hold a member's message, and never raises
(`test_the_event_error_handler_never_raises`).

**The framework's refusal and fault types**, each caught by name:

| Type | Where | What it is |
|---|---|---|
| `Refused` | `framework/models/refusal.py` | a refusal whose text the member reads; subclasses `ValueError` |
| `SwitchNotSignedUp` | `framework/services/switches/module_service.py` | a fault in the bot's assembly (`architecture.md`, "How modules and core fit together") |
| `StepFailedOnDiscord` | `framework/models/change.py` | a step's failure on Discord, carrying what the job keeps (the lines a picture was drawn with, its notices, or each picture's label and notices where it drew several, and the index of the line holding them, for the try that posts) and a clause for the stop notice |
| `GuildUnavailable` | `framework/models/change.py` | the league's server missing from the cache: a failure on Discord's side |
