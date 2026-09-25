# The bot's architecture

This file says how the bot's code is laid out, and why. It covers what applies to core and to
every module. Core's design file (`core.md`) and each module's (`results_module.md` and the rest)
cover their own part only, and point back here rather than repeat it.

It describes the shape the code is to have. Parts of the code do not have it yet: where a rule
below is checked by a test, the check lists what does not match today (see "How the rules are
checked"), and the rest of the work is tracked on GitHub. Where this file and the code disagree
about what the code *does*, the code wins and this file is corrected.

It holds no rule a league would notice. Those live in the specifications under
`docs/wip-specs/`, and this file points to them rather than repeat them.

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
a file is then plain from where it sits. The tests follow the same folders.
*Rejected:* grouping by layer first (`services/results/`, `cogs/results/` and so on), which
spreads one module over four folders. *Rejected:* grouping only the services. *Rejected:* staying
flat with a list of which file belongs where, which is the arrangement that misfiled core's
driver and team code under signup. *Rejected:* leaving the tests in one flat folder.

Core holds what every module shares: the database connection and the migrations, the bot object
(`LeagueBot`), the scheduler service, the base classes for the command tree, buttons and forms,
`report_failure`, and the log channel writer.

**The entry point** (`__main__.py`) sits above core and the modules, and is the one place that
imports every module at run time. It holds the builder that makes every service, loads every
module's cogs, signs each module up to core's hooks, and starts the bot. That is how core can offer
hooks without ever importing a module.

**What each part may do:**

- **A cog** turns a command, button or form into a call to one service, and replies. It holds no
  database code and no league rule, so every rule a league relies on can be tested by calling a
  service with no Discord interaction involved. A cog holds one command group; when one grows too
  large, its workflows move into services before it is split.
- **A service** holds the league's rules and all the database code. It may use discord.py: it can
  post to a channel and build the buttons it posts. Keeping Discord out of the services entirely
  would need a separate posting layer, which a bot this size has no use for. A service never
  imports a cog.
- **Buttons live beside the code that posts them:** with the cog when a command posts them, with
  the service when a service does. A service never reaches into a cog for a view.
- **Shared helpers** in core use no services, apart from the bot's type naming them (below).
  Core, in turn, uses no module.
- **A model** is plain data. It uses no Discord and no database.
- **The database code in core** opens connections and applies migrations, and imports nothing else
  of the bot's.
- **The entry point** builds the services, wires them together and starts the bot. It holds no
  rules and no database code of its own.

**Services stay attached to the bot, and are built in one place.** The bot object keeps a typed
attribute for each service object. Most services are plain functions and need none. The builder
makes every service object, handing each the other services it needs, and a service is complete
once it is built: nothing is wired into it afterwards. A test can call the builder to get the real
set of services. A service that needs Discord itself, to post or to find a channel, is given the
bot for that and nothing more. It does not use the bot to look up other services, and a service
that is a plain function is handed the services it needs by whoever calls it. Looking services up
on the bot is what lets a hand-made fake bot in a test decide which code runs.
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
(a round amended or cancelled, placements confirmed, a review gathering its lines, a season
ending, a module turned off), core declares a hook. The entry point signs each module up to it when
the bot starts. Core calls whoever signed up, and never imports a module.
*Rejected:* only writing the dependencies down as a table, which leaves each module's switch-off
code in core. *Rejected:* registering a module object for each module, more machinery than a
handful of modules need.

Each module still checks its own on/off switch where its own work starts.
`tests/unit/test_attendance_module_gate.py` pins that for attendance. A hook does not replace the
switch. It only means core no longer needs to know the module is there.

The image module is used by the others to draw their posts, and a module that finds it turned off
posts text instead, as the image module specification's "Configuration" section (the
`images config toggle` and its fallback) says. The image module draws what a module hands it, and
imports no other module. Every other module may call the image module without an entry in the
table. Core reaches it through a hook, so core's own graphics (the calendar, the lineup) are asked
for rather than drawn by importing it. Where a module must let one that depends on it act (results
after a round, for attendance), it does so through a hook too.

**Each module turns itself on and off.** A module's service has the code that turns it on and off,
next to the code that sets up what turning it off must remove. Kept apart, a switch-off comes to
miss what setup added: signup once set four permissions on its channel and its switch-off cleared
three. The `/module` commands in core only handle the command itself, the confirmation, and
anything the dependency table says must go with it. What turning a module on or off must do is set
by the constitution's Principle X and by each module's specification, not here.

**A module's private code stays inside it.** A name starting with an underscore is used only
inside its own module (after PEP 8's convention, extended to the files of one module). What a
module offers the others is public, and its docstring says what it promises.

---

## How a change is carried out

**Every change goes through one queue.** Every change to the bot's data, whether a person asks for
it (a command, a button, a form, a typed answer) or a timer does, is put on one queue and carried
out one at a time. Commands that only read do not use it. Carrying out changes on the spot is what
left data half-changed when two presses overlapped, the bot stopped part-way or a step failed, and
what let a success be reported over a change only half done.
*Rejected:* putting only the championship changes, or only the long approvals, through the queue,
which leaves every other change with the same risks. *Rejected:* each command writing its own
audit record, which is how some settings came to have none.

- **A person asks, or a timer fires.** Either way the change is put on the queue. What the person
  who asked is told, and when, is the core specification's. Discord lets a reply be updated for 15
  minutes after the member acts (the life of an interaction's token); a change that finishes later
  still has its outcome in the log channel.
  *Rejected:* making whoever asked wait for the result, which shows them nothing until the end,
  and nothing at all if the change takes longer than those 15 minutes.
- **Checked before it is queued.** Whatever can be checked at the start is checked (the channel
  exists, the bot may post there, the text fits, the change is allowed in the season's current
  stage), and the request is refused if not. That is the gate `steward_module.md` §4 designs for a
  cycle's close, made bot-wide.
- **One change at a time.** One worker runs one change at a time, across the whole bot, since
  SQLite lets one writer in at a time anyway. It takes changes in the order they were asked for,
  the one exception being a change waiting on a retry (below). Two presses of the same button can
  no longer run into each other, and a request for a change already waiting, running or done is
  refused.
- **Steps, each saved.** A change is made of steps: save this, post that, save the next. Each step
  is recorded as done when it finishes. After a restart, the worker carries on from the last
  finished step, and nothing already done is done again.
  *Rejected:* starting a cut-off change again from the top.
- **Posts are remembered.** A post a step makes is recorded with its message id, so repeating the
  step replaces the post rather than adding a second. The one gap: if the bot stops after a post is
  sent but before it is recorded, repeating the step sends it again, and a post that replaced an
  earlier one leaves its first copy behind, a known gap against Constitution XIV rule 8 ("at most
  one such message stands at any moment"). Scanning the channel to spot that is not done:
  `steward_module.md` §7 rejects scanning a channel as guesswork, and that reasoning carries over.
- **A failed step is retried, without holding up the queue.** A step that fails because of Discord
  is retried with growing waits, by running the owner's post again, as text where it would have
  been a picture (Constitution XIV, rule 8). While it waits, it steps aside: a later change to the
  same round or division, or to the season's own record (its stage, its settings), waits behind
  it, and changes to other rounds and divisions go ahead. It is retried until it succeeds, and
  reported to the log channel if it keeps failing, as the core specification's "When the bot
  stops" requires of a failed post.
  *Rejected:* stopping a change at its first failed step.
- **The record is the queue's.** The queue writes the audit record and the log-channel line for
  every change, so no command can forget them.

---

## The database

**All database code is in services**, or in core's database code for connections and migrations.
A cog never opens a connection, and neither does the entry point.

**A step saves in one go.** A step that saves opens its connection, makes its changes, and either
commits them all or none. It does not hand its connection back to whoever called it. When one
module has to change another module's table in the same step, it calls that module's service and
passes its own connection down, so the step still happens all at once.

**The queue writes a change's audit record** in the same save as the step that makes the change,
and its log-channel line once the change is done, saying how it turned out. The core
specification's "The record of what changed" asks for both.

**While a save is open, the bot waits on nothing but that save's own connection.** Not on Discord,
and not on a second connection. SQLite lets one writer in at a time, so a post made in the middle
of a save holds up every other save in the bot for as long as Discord takes to answer, and a second
connection that tried to write would be kept waiting by the first until it gave up and failed.
Save first, then post, then record what was posted in a save of its own.

**Each table is written by one module.** Every statement that changes a table lives in the module
that owns it, and other modules ask that module to make the change. Each module's design file
lists its tables. Where a module keeps its own columns on a core table, as weather does on rounds
and sessions, attendance on rounds, and results on a round's status, the design file names those
columns as an exception.
*Rejected:* moving each module's columns off core's tables now.

**Where a rule goes: the schema or a service.**

- A rule about identity or reference (a round belongs to one division; the bot holds at most one
  live season) goes in the schema, as a key or a unique index.
- A fixed list of values lives in its Python enum. It is repeated as a CHECK in the schema only
  where the list is not expected to grow, and a test ties the two together, in
  `tests/unit/test_schema_rules.py`, which already ties the image defaults to their Python
  constants.
- A trigger only keeps tables of one module in step, or refuses a row that contradicts itself (as
  the season's stage triggers do).
- Everything else is service code.

Once the bot is live, a CHECK, or a key declared inside a table, can only be changed by rebuilding
the table, which is why this is settled before stewarding adds its tables.

**Deletes are proven by a test, and the database deletes linked data only where it is safe.**
Deleting a season, division, round or driver removes what hangs off it through lists written by
hand, and a list not updated when a module added a table has made a delete fail or leave rows
behind. A test fills a database with one row in every table, fails when a new table is not filled,
and runs every kind of delete against it. After that, a link may be changed to delete
automatically, one at a time, only where the linked rows plainly belong to what is being deleted.
Never where the link is there to stop a delete, as a placed driver's seat stops a team being
removed from under them. A module's design file names any link of its own that is there for that
reason.
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
bot does this in the shape `steward_module.md` §3 designs for stewarding.
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
where the kind is registered, and the start-up sweep applies it. Every job is armed with no limit on
how late it may run (`misfire_grace_time=None`), so the scheduler never drops one on its own and its
default never decides, as `steward_module.md` §3 sets out. Which choice is right is a rule a league
notices, so it belongs to the core specification ("When the bot stops").

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
2. applies the migrations, before connecting to Discord;
3. builds the services with the one builder;
4. runs the start-up sweep once, when Discord first connects;
5. starts the queue, which carries on with any change a stop cut off;
6. only then lets scheduled jobs run.

Tests can then run the whole start-up in order, instead of checking its order by searching the
source code.

**One sweep picks up the timed events that were missed.** It walks everything that came due while
the bot was down, in the order it would have happened, and applies each kind's declared choice: run
it late, or skip it. A timed event that makes a change puts it on the queue, like any other. Each
step of the sweep is kept separate, so one failing is reported and does not stop the rest.

**A change cut off by a stop is the queue's to finish,** not the sweep's (see "How a change is
carried out"). Approving a season, for example, is one change: its lineups, calendars and sheets
are its later steps, and a restart carries on with them.
*Rejected:* a separate record of work still owed after a save, beside the queue.

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

| Kind | Examples | What its handler looks after |
|---|---|---|
| **Log line** | configuration changes, outcomes, failures | posting to the log channel, and retrying a failed line; what a log line may hold (a mention names without notifying; a long record is split) is the core specification's "The record of what changed" |
| **Standing post** | calendar, lineup, standings, attendance sheet, forecast | remembering every message it posts, replacing the previous one, and on failure having the owner post it again |
| **Notice** | a cancellation notice, a verdict | remembering the message wherever it might later be edited or deleted |
| **Bot-owned channel** | a results submission channel, a driver's own signup channel | posts inside a channel the bot creates and later deletes whole; only a post the bot must find again, such as a prompt it replaces, is remembered |

The code that makes a post still builds its own text, pictures and buttons. The handler looks after
everything that happens once it is sent. The log line's handler is today's router,
`utils/output_router.py`.
*Rejected:* one gateway for every post, which would need to know every module's rules.
*Rejected:* one handler per channel, which repeats the same rules a dozen times.

**Every post keeps three things,** whichever handler sends it:

- **Its message id,** wherever the bot might later edit, delete or replace it. The post's handler
  saves the id at the time, against the owner's record (the row or table the owner names). A
  message nobody recorded can never be found again, to be edited or deleted.
- **Who it may mention,** stated where it is sent.
- **What happens if it fails:** it is retried, and named in the log channel if it keeps failing.

**A failed post is retried by its owner.** A post that fails inside a change is retried by the
change queue, which runs the owning module's post again, as text. The text is then written at the
moment it is finally sent, and the handler records the new message against the owner's record as
it always does. No queue ever holds a picture; the constitution's Principle XIV, rule 8, says why.
The old retry queue is kept for log lines only.
*Rejected:* the retry queue re-sending its stored text and writing the new message's id back
itself. *Rejected:* no longer retrying these posts. *Rejected:* keeping the retry queue for posts
beside the change queue, as two mechanisms for one job.

Which channels exist, and what may go in each, is the constitution's Principle VII and the channel
registry (`channel_registry_service`).

---

## Errors and failures

**Each kind of starting point has one failure path.** Whatever goes wrong, the full error goes to
the host's log and one line goes to the log channel, and reporting it never raises another error.
A command that asks for a change meets two in turn: `report_failure` if the request itself fails,
and the queue once the change is under way.

- **Changes**, from whatever starting point, are reported by the queue: the outcome to whoever
  asked, and a line to the log channel.
- **Commands, buttons and forms** go through `report_failure` (`utils/interaction_errors.py`),
  called by the `LeagueCommandTree`, `LeagueView` and `LeagueModal` base classes. What the member
  and the log channel are told is the core specification's "When a command fails".
- **Scheduled jobs and repeating loops** go through one job runner.
- **Discord events** (a message, a member leaving) go through one error handler on the bot.
- **Background tasks** go through the helper that starts them.

**No cog handles its own errors, and there is no shared base class for cogs.** Every failure a
command does not catch itself reaches `report_failure` through the command tree. The tree steps
aside for any command or cog that has its own error handler (`LeagueCommandTree.on_error` in
`utils/league_server.py`), so one added handler would quietly switch `report_failure` off for
everything it covers.
*Rejected:* a `LeagueCog` base class. It would hold two lines of code, and an error handler added
to it would switch off the one failure path for every command.

**A catch-all error handler** (`except Exception`) is allowed in only four places:

1. on one of the failure paths above;
2. where the bot works through a list (divisions, drivers, posts) and one item failing must not
   stop the rest. The failure becomes a line in the result, which the command reports to the
   manager, and its log line says the job did not complete, as the results module specification's
   "A republication that does not land shall be reported" requires of a republication. This does
   not apply where an operation's own rule is all or nothing, as a points amendment's is (the
   results specification's "Changing points system mid-season");
3. around reporting a failure, where the report itself might fail;
4. around a clean-up that then raises the error again.

In every case it keeps the full error details for the host's log. A command catches only the errors
it expects by name, and only to turn them into a refusal the member can act on.

---

## One bot, one league, one server

The bot serves one league on one Discord server. `utils/league_server.py` explains how, in its
docstring. In short: the server is checked once, where a command, button, form or event first
arrives, and nowhere after. Below that, nothing in the bot is told which server it is on. The tables
carry no server id except the one settings row, and the services take none, because the database
only ever holds the one league. `tests/unit/test_one_league_server.py` and
`test_no_server_id_is_left_outside_server_configs` in `tests/unit/test_schema_rules.py` hold this
in place.

The owner-only `!sync` command in the entry point is the one command that skips the check. It only
refreshes Discord's list of commands and touches no league data.

---

## Where the other rules already live

These rules bind every module but are written down elsewhere. They are linked here, not copied, so
they cannot drift apart:

- **The base classes** for commands, buttons and forms: `LeagueCommandTree`, `LeagueView` and
  `LeagueModal`, in `utils/league_server.py`. `tests/unit/test_one_league_server.py` checks that
  no view or form derives from discord.py's own classes directly.
- **The failure path for commands, buttons and forms**: `report_failure`, in
  `utils/interaction_errors.py`.
- **The coverage floor for each module, and the single schema baseline until go-live**: CLAUDE.md,
  under "Testing". The values are in the CI workflow and the `run_migrations` docstring.
- **The type check**: CLAUDE.md and `mypy.ini`.
- **Staging files by name, and moving files with `git mv`**: CLAUDE.md, under "Working
  conventions".

---

## How the rules are checked

Where a rule can be checked by reading the code, a test checks it and fails the build. import-linter
checks which code may import which, and our own tests check the rest.
*Rejected:* our own tests alone. *Rejected:* adopting ruff now, which adds a second way of checking
some of the same things, and, with its formatter, a commit touching nearly every file. It may come
later on its own.

- **`.importlinter`**, run by `tests/unit/test_import_contracts.py`, checks which code may import
  which.
- **`tests/unit/test_architecture_rules.py`** checks the rest: database code outside services,
  awaiting anything but the connection mid-save, cogs handling their own errors, catch-all handlers
  that lose the error details, background tasks nobody keeps, code reaching past the scheduler
  service, jobs armed with a lateness limit, private names used across modules, a new place naming
  the log channel (the check lists the six allowed, with their reasons), and posting outside the
  handlers.
- **`tests/unit/test_one_league_server.py`**, **`test_schema_rules.py`** and
  **`test_import_roots.py`** check the one-league rule, the schema's own rules, and that nothing
  imports the bot through a package named `src`.

Both import-linter and the architecture test run with the ordinary test suite, on every host and in
CI. Where today's code breaks a rule, each check lists the breach with the work that will remove it,
and the list can only get shorter; how to work with the lists is CLAUDE.md's, under "Testing".
Once the queue exists, a further check holds that nothing writes to the database outside a queued
change. The rules about how things are built (the hooks, the one builder, start-up, the sweep)
cannot be checked by reading the code until they exist.
