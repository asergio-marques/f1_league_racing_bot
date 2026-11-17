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
(`core.md`, "Services, and what each owns") and the framework names no core type.

**The framework's models** (`framework/models/`) are plain data: the change queue's vocabulary
(`change.py`), the refusal a member reads (`Refused`, `refusal.py`), what a start-up step's error
carries for its rounds (`RoundsFailed`, `recovery.py`), the league's settings row as the framework
reads it (`league_settings.py`) and a log line waiting to be delivered (`pending_message.py`).

**The framework's helpers** (`framework/utils/`) are the bot's type (`league_bot.py`), the
one-league claim and the base classes of the command tree, views and modals (`league_server.py`),
the hook types (`hooks.py`), the failure paths (`interaction_errors.py`, `answering.py`), the
standard log lines (`log_lines.py`), a member as the log channel names them (`member_names.py`),
the log filters, `bounded_autocomplete` (`autocomplete.py`), the guard that refuses a post made
outside a job (`in_a_job.py`), the wait for the catch-up after a restart (`catch_up_gate.py`), the
channel cast (`channels.py`) and the path resolution that keeps a path inside the project
(`paths.py`).

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
handed, and the decisions that govern them (a post's id saved with its mark, a step's four kinds, a
job that ends on a fault); `framework/services/queue/queue_records.py` holds the bookkeeping over
the queue's tables, what is in hand, what was ever asked, the job numbering and the emptying of a
restored database, which a check, a recovery, a restore and a factory reset call without a queue.
The queue file keeps the constants the class reads: `RETRY_AFTER`, the bot's own tries;
`CONVERSATION_LANES_AT_ONCE`; `UPDATABLE_FOR`, how long a reply may be updated;
`WORKER_PAUSE_AFTER_FAULT`; and `NOTICE_REPOST_EVERY`; with `_confine` and `QUEUE_TABLES`. The plain
data is in `framework/models/change.py`: `StepKind` (`SAVE`, `ACT`, `DELETE`, `EDIT`), `Verdict`,
`StepResult`, `StepFailedOnDiscord`, `GuildUnavailable` and the lane names. The engineering
decisions that govern the queue are in its module docstring, and those that govern a step's kinds,
its record and a fault's ending in `change_types`', each pinned by a test in
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

The framework owns four tables: the queue's `queued_changes` and `queued_change_steps`,
`audit_entries` and `pending_messages`. Core reads and writes them only through the framework's
services (`queue_records`, `audit_service`, `retry_service`), as `architecture.md`'s rules on
table owners have it.

| Constraint | Stated in | Pinned by |
|---|---|---|
| SQLite's busy timeout is five seconds, sized for the longest legitimate write; an autocomplete asks for a shorter one (`AUTOCOMPLETE_TIMEOUT_SECONDS`) | `framework/db/database.py`, `_BUSY_TIMEOUT_SECONDS` | `test_connections_carry_an_explicit_busy_timeout` |
| The database runs in WAL with `synchronous` left at FULL, durability chosen over speed | `_enable_wal` | `test_migrations_leave_the_database_in_wal_mode`, `test_connections_keep_full_durability` |
| Four idle connections are kept to be lent again | `_IDLE_CONNECTIONS_KEPT` | `test_no_more_than_four_idle_connections_are_kept` |
