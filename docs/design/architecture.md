# The bot's architecture

This file says how the bot's code is laid out, and why. It covers what applies to core and to
every module. Core's design file (`core.md`) and each module's (`results_module.md` and the rest)
are to cover their own part only, and point back here rather than repeat it.

It describes the shape the code is to have. Parts of the code do not have it yet: where a rule
below is checked by a test, the check lists what does not match today (see "How the rules are
checked"), and the rest of the work is tracked on GitHub. Once the code has been brought to it,
where this file and the code disagree about what the code *does*, the code wins and this file is
corrected.

It holds no rule a league would notice, except where the shape of the code decides something a
league sees, such as the names of commands or what waits and what is refused; each of those is
written into its specification too, when the work that builds it lands. The rest live in the
specifications under `docs/wip-specs/`, and this file points to them rather than repeat them.

---

## How the code is laid out

Everything is in one package, `leaguebot`, installed into the virtualenv through a
`pyproject.toml` and started with `python -m leaguebot`. The tests then import the bot exactly as
it runs, from one place; when the tests and the bot see the code from two different roots, an
import can work under test and fail in the bot. Folder names like `results` or `image` also cannot
clash with an installed library of the same name.

*Rejected:* keeping each folder as a top-level package of its own. *Rejected:* setting the path
in the start command instead of installing.

There is one folder for core and one for each module:

```
leaguebot/
    core/         the shared plumbing, and core's own commands and rules
    results/
    attendance/
    signup/
    weather/
    image/
    steward/      once it is built
    __main__.py   the entry point: builds everything and starts the bot
```

Each folder holds that part's **cogs** (its commands, and the buttons and forms they post), its
**services** (its rules and its database code) and its **models** (plain data). Which module owns
a file is then plain from where it sits. The tests follow the same folders, one for core and one
for each module under `tests/`, beside `tests/repository/` for the checks on the repository itself
(these rules, its configuration and its tools) and `tests/support/` for the helpers the tests
share.

Inside each folder, each kind of code has a folder of its own: `cogs/`, `services/`, `models/`,
and `utils/` for the part's helpers, with `db/` in core for the database code. What kind of code a
file holds is then plain from where it sits as well, so a rule about one kind (no service imports
a cog) is written once for every module, and the checks tell a service from a cog by its folder.

*Rejected:* a module's files side by side in its folder, where a file's kind is known only from
its name, and several files are named for neither.

*Rejected:* grouping by layer first (`services/results/`, `cogs/results/` and so on), which
spreads one module over four folders. *Rejected:* grouping only the services. *Rejected:* staying
flat with a list of which file belongs where, which is the arrangement that misfiled core's
driver and team code under signup. *Rejected:* leaving the tests in one flat folder.

Core holds what every module shares: the database connection and the migrations, the bot object
(`LeagueBot`), the change queue, the scheduler service and the start-up sweep, the base classes
for the command tree, buttons and forms, the failure paths (`report_failure`, the job runner, the
bot's error handler for events, the helper that starts background work), and the handlers for
each kind of post, the log line's among them.

**The entry point** (`__main__.py`) sits above core and the modules, and is the one place that
imports every module at run time. It holds the builder, which makes every service, loads every
module's cogs, signs each module up to core's hooks and registers each kind of timed job with the
scheduler service, all before the bot connects. It then starts the bot. That is how core can offer
hooks without importing a module.

**What each part may do:**

- **A cog** turns a command, button or form into a call to one service, and replies. It holds no
  database code and no league rule, so every rule a league relies on can be tested by calling a
  service with no Discord interaction involved. A cog holds one command group; when one grows too
  large, its workflows move into services before it is split. A module's commands sit under its own
  top-level group, never under one of core's: `/weather channel`, not `/division weather-channel`.
  So do its tools for test mode, which sit under the module's group and work only while test mode is
  on, rather than under `/test-mode`. A command's name then says which module owns it, and the
  module's code stays in its folder. discord.py binds a subcommand only to the cog that declares its
  group (`steward_module.md` §5), so a module's command under core's group would have to be written
  in core's cog, or bound to the module by hand.

  *Rejected:* core's cog holding a module's commands and handing the work to the module through a
  hook, which puts part of every module in core. *Rejected:* a module binding its own commands to
  core's groups as it loads, which works, but leaves a module's commands under names that are
  core's.
- **A service** holds the league's rules and all the database code. It may use discord.py: it can
  post to a channel and build the buttons it posts. Keeping Discord out of the services entirely
  would need a separate posting layer, which a bot this size has no use for. A service never
  imports a cog.
- **Buttons live beside the code that posts them:** with the cog when a command posts them, with
  the service when a service does. A service never reaches into a cog for a view.
- **Shared helpers in core** are of two kinds. Those used by cogs (the base classes, the guards,
  autocomplete) reach core's own services through the bot, as a cog does. Plain helpers
  (formatting, parsing, time) use no service at all. Neither imports a service, and core uses no
  module; the one exception to both is the bot's type, which names every service for the type
  checker (below). The log line's handler is a service in core, not a helper.
- **A module's own helpers** sit in its `utils/` folder, beside its services, under the same rules.
- **A model** is plain data. It uses no Discord and no database, and no service or cog.
- **The database code in core** opens connections and applies migrations, and imports nothing else
  of the bot's.
- **The entry point** builds the services, wires them together and starts the bot. It holds no
  rules and no database code of its own.

**Services stay attached to the bot, and are built in one place.** The bot object keeps a typed
attribute for each service object. Most services are plain functions and need none. The builder
makes every service object, handing each the other services it needs, and a service is complete
once it is built: nothing is wired into it after the bot starts. A test can call the builder to
get the real set of services. A service that needs Discord itself, to post or to find a channel,
is given the bot for that and nothing more. It does not use the bot to look up other services,
and a service that is a plain function is handed the services it needs by whoever calls it.
Looking services up on the bot is what lets a hand-made fake bot in a test decide which code
runs.

*Rejected:* a separate container object holding the services, since the services still need the
bot itself for Discord, so it would add a second object without removing the first.

*Rejected:* leaving the services as they were, where a hand-made fake bot has decided which code
path ran and a service wired in two steps has failed between the steps.

Because the services stay on the bot, the bot's type (`LeagueBot`) names every service object. It
does so for the type checker only, under `TYPE_CHECKING`, and nothing runs because of it. That is
the one place core names the modules' services, and the price of keeping them on the bot.
`.importlinter` lists it as a stated exception rather than a breach.

---

## How modules and core fit together

**Modules depend on core, never the other way round.** A module may use another module only where
the dependency table says so. The table is written once and read wherever it matters: when a module
is turned on, when one is turned off, and when a warning names what will be switched off with it.
It starts with one entry, attendance needing results (the attendance module specification's
opening rules). Stewarding adds a second [STW-MOD-007].

**Core reaches a module only through hooks.** Where something in core has to let the modules act
(the bot starting, a round amended or cancelled, placements confirmed, a review gathering its lines,
a season ending, a module turned on or off, the hub's panel gathering its options), core declares a
hook. The builder signs each module up to it before the bot connects. Core calls whoever signed up,
and never imports a module, the bot's type aside (see "How the code is laid out").

*Rejected:* only writing the dependencies down as a table, which leaves each module's switch-off
code in core. *Rejected:* registering a module object for each module, more machinery than a
handful of modules need.

Each module still checks its own on/off switch where its own work starts.
`tests/attendance/test_attendance_module_gate.py` pins that for attendance. A hook does not replace
the switch. It only means core no longer needs to know the module is there.

The image module is used by the others to draw their posts, and a module that finds it turned off
posts text instead, as the image module specification's "Configuration" section (the
`images config toggle` and its fallback) says. The image module draws what a module hands it, and
imports no other module. Every other module may call the image module without an entry in the
table. Core reaches it through a hook, so core's own graphics (the calendar, the lineup) are asked
for rather than drawn by importing it. Where a module must let one that depends on it act (results
after a round, for attendance), it does so through a hook too.

**Each module turns itself on and off.** A module's service has the code that turns it on and off,
next to the code that sets up what turning it off must remove. Kept apart, a switch-off comes to
miss what setup added, as signup's switch-off came to leave behind the permission its setup gives
the league admin role on its channel. The `/module` commands in core only handle the command itself,
the confirmation, and anything the dependency table says must go with it. What turning a module on
or off must do is set by the constitution's Principle X and by each module's specification, not
here.

**A module's private code stays inside it.** A name starting with an underscore is used only
inside its own module (after PEP 8's convention, extended to the files of one module). What a
module offers the others is public, and its docstring says what it promises.

---

## How a change is carried out

**Every change goes through one queue.** Every change to the bot's data is put on one queue and
carried out one at a time, whoever or whatever asks for it: a person (a command, a button, a form,
a typed answer), a timer, a Discord event (a member leaving), or the start-up sweep. Commands that
only read do not use it. Carrying out changes on the spot is what left data half-changed when two
presses overlapped, the bot stopped part-way or a step failed, and what let a success be reported
over a change only half done.

*Rejected:* putting only the championship changes, or only the long approvals, through the queue,
which leaves every other change with the same risks. *Rejected:* each command writing its own
audit record, which is how some settings came to have none.

- **The queue is kept in the database,** so a change waiting on it survives a stop.
- **What whoever asked is told,** and when, is for the core specification, which gains it with the
  queue. The design allows for Discord letting a reply be updated for only 15 minutes after the
  member acts (the life of an interaction's token): an outcome that comes later goes to the log
  channel instead, and whatever the specifications ask to be recorded of a change goes there
  however long the change takes.

  *Rejected:* making whoever asked wait for the result, which shows them nothing until the end,
  and nothing at all if the change takes longer than those 15 minutes.
- **Checked before it is queued, and again before it runs.** Whatever can be checked is checked (the
  channel exists, the bot may post there, the text fits, the change is allowed in the season's
  current stage): once when it is asked for, and again when the worker takes it up, since changes
  ahead of it may have moved the season on. A request a person made that fails is refused. A change
  a timer, an event or the sweep asked for is dropped once its work is no longer due, as a job that
  fires after its work was cancelled does nothing (see "Timed work and restarts"). Where it fails a
  check the league can repair (a channel, set by a command; the bot's permission, restored in
  Discord), it waits instead, and is retried the way a step that failed because of Discord is
  (below); meanwhile the log channel records what failed, as the core specification's "Setting the
  bot up" has it do. Any other check it fails is a fault (below). This is the gate
  `steward_module.md` §4 designs for a cycle's close, made bot-wide.
- **All or nothing in one step.** Where a change must be all or nothing (as the results
  specification's "Changing points system mid-season" requires of an approval), everything it
  saves is saved in one step behind the gate, and only its posts come after, as
  `steward_module.md` §4 does for the cycle close.
- **One change at a time.** One worker runs one change at a time, across the whole bot, since
  SQLite lets one writer in at a time anyway. It takes changes in the order they were asked for,
  the exception being a change waiting to be retried (below). Two presses of the same button can
  no longer run into each other. A change's posts hold the worker while they are sent, so a long
  run of posts delays the changes behind it; that is the price of one change at a time.
- **The same change is not queued twice in a row.** A change is named by what it does, what it acts
  on and the values it sets (approving a round's appeals, reposting a division's calendar, setting a
  division's channel to a given one). A request for the same change as the last one waiting is not
  queued again, so a second press does nothing. A change that can only be done once, such as
  approving a round's appeals, is refused by the checks once it has been done, since the round has
  moved on. One that may be repeated, such as a repost, is queued again once it is running or once
  anything else waits behind it, since a repost reads what it posts when it runs.
- **Steps, each saved with its mark.** A change is made of steps. The worker opens each saving
  step's connection and hands it down, so the step's changes, the change's audit record where it
  has one, and the mark saying the step is done all commit together or not at all. After a
  restart, the worker carries on from the first step not marked done, and nothing marked done is
  done again.

  *Rejected:* starting a cut-off change again from the top.
- **A restored state brings back no queue.** Restoring a saved state (the core specification's
  "Saving a state and returning to it") empties the queue that comes back with it, so a change under
  way when the state was saved is not resumed.
- **Posts are remembered.** A posting step sends the post, then saves its message id with the step's
  mark. Where the post replaces an earlier message, deleting that message is a step of its own,
  after it, reading the old message's id, which the posting step saved beside the new one, so the
  new post stands before the old one goes (Constitution XIV rule 8). Two windows remain. If the bot
  stops after a post is sent but before its id is saved, the step runs again after the restart and
  sends a second copy, and the first copy is left behind. And the old and new messages stand
  together between a post and the deletion after it: until the change finishes on restart, if the
  bot stops in between, or for as long as the deletion waits on a retry. The design accepts both,
  and Constitution XIV rule 8 ("at most one such message stands at any moment") is amended with the
  queue to allow them. Scanning the channel to spot such a copy is not done: `steward_module.md` §7
  rejects scanning a channel as guesswork, and that reasoning carries over.
- **A step that fails because of Discord is retried, without holding up the queue.** It is retried
  with growing waits, up to a fixed ceiling, by running the owning module's post again, as text
  where it would have been a picture (Constitution XIV, rule 8). While it waits, it steps aside: a
  later change that would post to the same place, or change what the waiting step is about to post,
  waits behind it, and every other change goes ahead. Two kinds of change are never held behind it.
  A command that repairs what the step needs, such as setting a channel, runs, and the waiting step
  is tried again at once, as `steward_module.md` §4 has the channel-setting commands resume a
  waiting close; a repair made in Discord, such as restoring the bot's permission, is found at the
  next try. And a change that makes the waiting step's work no longer due, such as cancelling its
  round or turning its module off, runs, and the waiting change is checked again after it: its steps
  whose work is no longer due are dropped and the rest go ahead, since switching a module off stops
  that module's work and no other's (the core specification's "Modules"). A step that keeps failing
  is reported to the log channel, as the core specification's "When the bot stops" requires of a
  failed post, in the form the owning specification asks for (for a republication, the results
  specification's "A republication that does not land shall be reported", with the commands that
  finish the job), and it is still retried. Discord answering that a message is already gone is no
  failure: a step that deletes it is done, and one that edits it is done too, with a line in the log
  channel saying the message was gone.

  *Rejected:* stopping a change at its first failed step.
- **A step that fails for any other reason is a fault.** The change stops there and goes to the
  failure path (see "Errors and failures"), and holds nothing up. What its earlier steps saved
  stays saved, which is why anything that must be all or nothing is saved in one step.
- **The record is the queue's.** The queue writes the records the specifications ask of a change:
  for every change to the league's configuration, an audit record and a log-channel line (the core
  specification's "The record of what changed"), and for any other change, whatever log line its own
  specification asks for. No command writes them itself. A log line is put on the log line's own
  retry queue in the same save as the step it records, so a stop cannot lose it.

---

## The database

**All database code is in services**, or in core's database code for connections and migrations.
A cog never opens a connection, and neither does the entry point.

**A step saves in one go.** The queue opens a saving step's connection and hands it down; the step
makes its changes on it, and they commit, with the step's mark and any audit record, all together
or not at all. Nothing hands a connection back up to its caller. When one module has to change
another module's table in the same step, it calls that module's service with the connection it was
handed, so the step still happens all at once.

**While a save is open, the bot waits on nothing but that save's own connection.** Not on Discord,
and not on a second connection. SQLite lets one writer in at a time, so a post made in the middle
of a save holds up every other save in the bot for as long as Discord takes to answer, and a second
connection that tried to write would be kept waiting by the first until it gave up and failed.
Save first, then post, then record what was posted in a save of its own.

**Each table is written by one module.** Every statement that changes a table lives in the module
that owns it, and other modules ask that module to make the change. Each module's design file lists
its tables, and the check of this rule holds the owner of every table (see "How the rules are
checked"), failing on a table added without one. Where a module keeps its own columns on a core
table (weather on rounds, sessions and a division's forecast channel, attendance on rounds, and the
weather and signup on/off flags on the settings row), that module's design file names those columns
as an exception. The other modules keep their on/off flag in their own settings table. A round's
status is core's, though results moves it through the stages of a round's results: results asks
core's service to set it, so a round can still be cancelled while results is switched off.

*Rejected:* moving each module's columns off core's tables now, which touches every reader of those
columns before go-live for a cleaner line of ownership.

**Where a rule goes: the schema or a service.**

- A rule about identity or reference (a round belongs to one division; the bot holds at most one
  live season) goes in the schema, as a key or a unique index.
- A fixed list of values lives in its Python enum. It is repeated as a CHECK in the schema only
  where the list is not expected to grow, and a test ties the two together, in
  `tests/core/test_schema_rules.py`, which already ties the image defaults to their Python
  constants.
- A trigger only keeps tables of one module in step, fills in a value a new row left out, or
  refuses a row that contradicts itself (as the season's stage triggers do).
- Everything else is service code.

Once the bot is live, a CHECK, or a key declared inside a table, can only be changed by rebuilding
the table, so which rules go in the schema is settled before more tables are added.

**Deletes are proven by a test, and the database deletes linked data only where it is safe.**
Deleting a season, division, round or driver removes what hangs off it through lists written by
hand, and a list not updated when a module added a table has made a delete fail or leave rows
behind. A test fills a database with one row in every table, fails when a new table is not
filled, and runs every kind of delete against it. After that, a link may be changed to delete
automatically, one at a time, only where the linked rows plainly belong to what is being
deleted. Never where the link is there to stop a delete, as a placed driver stops their division
being deleted (the core specification's "Divisions"). A module's design file names any link of
its own that is there for that reason.

*Rejected:* letting the database delete linked data almost everywhere, which can quietly remove
history. *Rejected:* only writing down how each link should behave, which would never run a
delete.

**A database update is applied whole, or not at all.** Each migration runs in one transaction with
foreign keys switched off, and the keys are checked before it commits. This is SQLite's own
procedure for changing a table ("Making Other Kinds Of Table Schema Changes", in its ALTER TABLE
documentation). With the keys switched on, rebuilding a table would silently delete the rows linked
to it by a cascade, empty the links set to be emptied, and fail on the rest. The rules for the
schema baseline itself are CLAUDE.md's.

**What going live changes for the schema is listed in one place,** the `run_migrations` docstring,
so nothing is missed on the day.

**A column nothing reads is removed, not kept.** A stored value nobody reads still has to be
written, and goes stale without anyone noticing.

---

## Timed work and restarts

The bot is mostly driven by the clock: weather phases, check-in calls and deadlines, results
channels opening, clean-ups. It also has to carry on correctly after it has been stopped. The whole
bot does this in the shape `steward_module.md` §3 designs for stewarding: the database holds the
moment work is due, a job only wakes the bot, no job is dropped for being late, and one sweep at
start-up works through what was missed, in order, before the scheduler runs. §3's heartbeat and
downtime extension stay stewarding's own.

*Rejected:* using this shape for stewarding only, which leaves the bot with three different ways
of recovering: the entry point's own steps, the results module's "results posted" flag, and
stewarding's own design. *Rejected:* reporting half-done work for a league manager to repair by
hand instead of finishing it.

**The database says when something is due.** A timed event is a row with the moment it is due. The
scheduled job (APScheduler) only wakes the bot at that moment. The code it runs reads the row again
and acts only if the row still says the work is due. A job that fires after its work was changed or
cancelled then does nothing. The job store becomes a convenience: if it were lost, the bot could
rebuild every job from the database.

**Each kind of job says what happens if it is missed.** When the bot was down at the moment a job
was due, the job either runs late or is skipped. That choice is declared once for each kind of job,
where the kind is registered, and the start-up sweep applies it. A skip is recorded on the event's
row, so the late job finds nothing due when the scheduler runs it. Every job is armed with no limit
on how late it may run (`misfire_grace_time=None`), so the scheduler never drops one on its own and
its default never decides, as `steward_module.md` §3 sets out. Which choice is right is a rule a
league notices, so it belongs to the core specification ("When the bot stops").

**Jobs are made only through the scheduler service.** Nothing else arms, finds or removes a job. A
round's job is named from the round and the event, so arming it again replaces the old one instead
of adding a second (the `_round_job_suffix` docstring in the scheduler service). One function works
out when each of a round's events falls, and one function arms them. Every path that arms a round (a
season approved, a round amended, a restart, test mode) uses those two, so the timings cannot drift
apart between copies.

**Start-up runs once, in one function.** discord.py can report the bot as "ready" more than once in
a single run, after a network drop, so start-up work does not belong in the handler for that event.
Start-up:

1. reads the settings (the token, the database path), in the entry point rather than on import;
2. swaps in a saved state waiting to be restored, before anything opens the database or the job
   store (the core specification's "Saving a state and returning to it");
3. applies the migrations, before connecting to Discord;
4. runs the builder: the services, the cogs, the hooks and the kinds of timed job;
5. runs the start-up sweep once, when Discord first connects;
6. starts the queue, which carries on with any change a stop cut off;
7. only then lets scheduled jobs run.

Tests can then run the whole start-up in order, instead of checking its order by searching the
source code.

**One sweep picks up the timed events that were missed.** It walks everything that came due while
the bot was down, in the order it would have happened, and applies each kind's declared choice: run
it late, or skip it. A timed event that makes a change puts it on the queue, like any other. A
module's start-up work that is no missed event (a review posted again, an interrupted submission
reopened, the hub's panel posted again, as the core specification's "When the bot stops" lists) is a
step of the sweep too, reached through the hook for the bot starting. Each step of the sweep is kept
separate, so one failing is reported and does not stop the rest.

**The sweep is core's, and the work is each module's.** The sweep decides which timed events are
due and in what order, and hands each to the handler its module provides for that kind of job. The
builder signs each handler up with its kind of job, beside the kind's missed-run choice. The handler
does the module's own work, and core holds no module's catch-up code: each module writes the
handler for every kind of timed job it has, and its catch-up work moves out of the entry point into
those handlers.

**A change cut off by a stop is the queue's to finish,** not the sweep's (see "How a change is
carried out"). Approving a season, for example, is one change: its lineups, calendars and sheets
are its later steps, and a restart carries on with them.

*Rejected:* a separate record of work still owed after a save, beside the queue, as two mechanisms
that would have to agree.

**Background work is started through one helper** that keeps hold of it. Python only keeps a weak
hold on a task nobody stores (asyncio's documentation says so), so such a task can vanish part-way,
and its failure goes unseen.

**Nothing blocks the bot.** Slow work (drawing a graphic, checking the templates, reading a large
file) runs off the bot's main loop, so commands and buttons keep being answered while it runs.

**The time is always passed in.** A service that needs the current time takes a `now` argument, and
every time inside the bot is in UTC. How tests treat `now` is CLAUDE.md's.

---

## Posting to Discord

**One small handler for each kind of post.** The posts the bot makes fall into four kinds, and each
kind has its own rules for failures and for later changes:

| Kind | Examples | What sets it apart |
|---|---|---|
| **Log line** | configuration changes, outcomes, failures | sent to the log channel and retried by its own handler, not by the change queue; what a log line may hold is the core specification's "The record of what changed" |
| **Standing post** | calendar, lineup, standings, attendance sheet, forecast | each new one replaces the last, and the old message is deleted after the new one is up |
| **Notice** | a cancellation notice, a verdict | its id is kept wherever it might later be edited or deleted |
| **Bot-owned channel** | a results submission channel, a driver's own signup channel | deleted whole with its channel; only a post the bot must find again, such as a prompt it replaces, has its id kept |

The code that makes a post still builds its own text, pictures and buttons, and saves the message id
it is handed back. The handler does the sending: it applies who the post may mention, hands back the
message id, deletes or edits a message when asked, and says whether a failure was Discord's.
Retrying is the change queue's, except for log lines. The log line's handler is today's router,
`core/utils/output_router.py`.

*Rejected:* one gateway for every post, which would need to know every module's rules.
*Rejected:* one handler per channel, which repeats the same rules a dozen times.

**Every post keeps three things,** whichever handler sends it:

- **Its message id,** wherever the bot might later edit, delete or replace it. The owning module
  saves the id the handler hands back in the posting step, and asks the handler to delete the
  message it replaces in the step after, so each table keeps one writer. A message nobody recorded
  can never be found again, to be edited or deleted.
- **Who it may mention,** stated where it is sent.
- **What happens if it fails:** it is retried, and named in the log channel if it keeps failing (a
  log line that keeps failing, in the host's log).

**A failed post is sent again by its owning module, on the queue's retry.** A post that fails inside
a change is retried by the change queue, which runs the owning module's post again, as text. The
text is then written at the moment it is finally sent, and the owning module saves the new message's
id as it always does. No
queue ever holds a picture; the constitution's Principle XIV, rule 8, says why. The old retry
queue is kept for log lines only.

*Rejected:* the retry queue re-sending its stored text and writing the new message's id back
itself. *Rejected:* no longer retrying these posts. *Rejected:* keeping the retry queue for posts
beside the change queue, as two mechanisms for one job.

Which channels exist, and what may go in each, is the constitution's Principle VII and the channel
registry (`channel_registry_service`).

---

## Errors and failures

**Each kind of starting point has one failure path.** Whatever fails in the end, the full error goes
to the host's log and one line goes to the log channel, and reporting it never raises another error.
A step still being retried has not failed yet. A change that succeeds is recorded in the log channel
as its specification asks; a failure always is. A command that asks for a change meets two in turn:
`report_failure` if the request itself fails, and the queue once the change is under way.

- **Changes**, from whatever starting point, are reported by the queue: the outcome to whoever asked
  while their reply can still be updated, and otherwise to the log channel (see "How a change is
  carried out"), and to the log channel as the change's specification asks.
- **Commands, buttons and forms** go through `report_failure` (`core/utils/interaction_errors.py`),
  called by the `LeagueCommandTree`, `LeagueView` and `LeagueModal` base classes. What the member
  and the log channel are told is the core specification's "When a command fails".
- **Scheduled jobs and repeating loops** go through one job runner.
- **Discord events** (a message, a member leaving) go through one error handler on the bot.
- **Background tasks** go through the helper that starts them.
- **The start-up sweep** goes through the job runner, one step at a time, like a scheduled job.

**No cog handles its own errors, and there is no shared base class for cogs.** Every failure a
command does not catch itself reaches `report_failure` through the command tree. The tree steps
aside for any command or cog that has its own error handler (`LeagueCommandTree.on_error` in
`core/utils/league_server.py`), so one added handler would quietly switch `report_failure` off for
everything it covers.

*Rejected:* a `LeagueCog` base class. It would hold two lines of code, and an error handler added
to it would switch off the one failure path for every command.

**A catch-all error handler** (`except Exception`) is allowed in only four places:

1. on one of the failure paths above;
2. where the bot works through a list (divisions, drivers, posts) and one item failing must not stop
   the rest. Inside a queued change, each item is a step: a Discord failure is retried, and a fault
   stops the change, so items that must each go ahead whatever befalls the others are queued as
   changes of their own. Outside one, the failure goes to the failure path of whatever started the
   work (above). Neither applies where an operation's own rule is all or nothing, as a points
   amendment's is (the results specification's "Changing points system mid-season");
3. around reporting a failure, where the report itself might fail;
4. around a clean-up that then raises the error again.

In every case it keeps the full error details for the host's log. A command catches only the errors
it expects by name, and only to turn them into a refusal the member can act on.

---

## One bot, one league, one server

The bot serves one league on one Discord server. `core/utils/league_server.py` explains how, in its
docstring. In short: the server is checked once, where a command, button, form or event first
arrives, and nowhere after. Below that, nothing in the bot is told which server it is on. The tables
carry no server id except the one settings row, and the services take none, because the database
only ever holds the one league. `tests/repository/test_one_league_server.py` and
`test_no_server_id_is_left_outside_server_configs` in `tests/core/test_schema_rules.py` hold this
in place.

The `!sync` command, which only the bot's owner may use, is the one command that skips the
check. It only refreshes Discord's list of commands and touches no league data.

---

## Where the other rules already live

These rules bind every module but are written down elsewhere. They are linked here, not copied, so
they cannot drift apart. The paths are today's, and move with the code:

- **The base classes** for commands, buttons and forms: `LeagueCommandTree`, `LeagueView` and
  `LeagueModal`, in `core/utils/league_server.py`. `tests/repository/test_one_league_server.py`
  checks that no view or form derives from discord.py's own classes directly.
- **The failure path for commands, buttons and forms**: `report_failure`, in
  `core/utils/interaction_errors.py`.
- **The coverage floor for each module, and the single schema baseline until go-live**: CLAUDE.md,
  under "Testing". The detail is in the CI workflow and the `run_migrations` docstring.
- **The type check**: CLAUDE.md and `mypy.ini`.
- **Staging files by name, and moving files with `git mv`**: CLAUDE.md, under "Working
  conventions".

---

## How the rules are checked

The rules are checked by tests that fail the build: import-linter checks which code may import
which, and our own tests check the rest.

*Rejected:* our own tests alone, which would write the import rules as hand-made walks rather than
declared contracts. *Rejected:* adopting ruff now, which adds a second way of checking
some of the same things, and, with its formatter, a commit touching nearly every file. It may come
later on its own.

- **`.importlinter`**, run by `tests/repository/test_import_contracts.py`, checks which code may
  import which: the rules for each kind of code, that core uses no module, that a module uses
  another only where the dependency table allows, and that nothing imports the entry point.
- **`tests/repository/test_architecture_rules.py`** checks the rest: database code outside services,
  awaiting anything but the connection mid-save, cogs handling their own errors, catch-all handlers
  that lose the error details, background tasks nobody keeps, code reaching past the scheduler
  service, jobs armed with a lateness limit, private names used across modules, a new place naming
  the log channel (the check lists the six allowed, with their reasons), posting outside the
  handlers, and a table written by a module that does not own it.
- **`tests/repository/test_one_league_server.py`**, **`tests/core/test_schema_rules.py`** and
  **`tests/repository/test_import_roots.py`** check the one-league rule, the schema's own rules,
  and that the bot is imported from one place: the installed package, never through `src`.

How the checks run, and how to work with the lists of today's breaches they hold, is CLAUDE.md's,
under "Testing".

Not every rule has its check yet. Once the queue exists, a further check holds that nothing writes
to the database outside a queued change, apart from the queue's own records (a change put on it,
dropped, or waiting on a retry), the migrations run at start-up and the retry queue for log lines.
The rules about how things are built (the hooks, the one builder, start-up, the sweep) cannot be
checked by reading the code until they exist.
