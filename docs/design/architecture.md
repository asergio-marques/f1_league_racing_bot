# The bot's architecture

This file says how the bot's code is meant to be laid out, and why. It covers what applies to
every module. Core's design file (`core.md`) and each module's (`results_module.md` and the
rest) cover their own part only, and point back here instead of repeating anything.

**This is a goal, not a description of today.** Much of the code does not match it yet. Every
place that does not match is listed under "Known divergences" at the end, with the issue that
will fix it. Once the code has caught up, the usual rule applies: where this file and the code
disagree about what the code does, the code wins and this file is corrected.

It holds no rule a league would notice. Those live in the specifications under
`docs/wip-specs/`, and this file points to them instead of repeating them.

It was settled in issue #282, on 25 September 2026.

---

## Decisions

Each decision was made by the project owner on 25 September 2026, unless it says otherwise.
What was turned down follows each one.

**1. Services stay attached to the bot, and are all built in one place.** The bot object
(`LeagueBot`) keeps a typed attribute for each service object, as it does today. Most services
are plain functions and need none. The objects are all built by one function, which a test can
call to get the real set of services. A service is fully set up once it is built: nothing is
wired in afterwards.
*Rejected:* a separate container object holding the services, since the services still need the
bot itself for Discord, so it would add a second object without removing the first.
*Rejected:* leaving things as they are, since hand-made fake bots in tests have picked the wrong
code path before (#240), and a service wired in two steps failed between them (the note where
`bot.py` wires the wizard, #228).

**2. No shared base class for cogs, and no cog handles its own errors.** Every command's
failure already reaches `report_failure` through the command tree. If a cog handled its own
errors, the tree would step aside and `report_failure` would never run. A test stops that.
*Rejected:* a `LeagueCog` base class. It would hold two lines of code, and an error handler
added to it would switch off the one failure path for every command.

**3. Code is grouped by module.** There is one folder for core and one for each module: results,
attendance, signup, weather, image, and stewarding once it is built. Each holds all of that
part's code: its cogs, services and models. Shared plumbing (the database connection, the base
classes, the log channel writer) belongs to core. Which module owns a file is then plain from
where it sits.
*Rejected:* grouping by layer first (`services/results/`, `cogs/results/` and so on), which
spreads one module over four folders. *Rejected:* grouping only the services. *Rejected:*
staying flat with a corrected list of which file belongs where, which is what failed before.

**4. The move into module folders is its own issue**, done straight after #282 and before
#283, so that every module pass starts from the new layout.
*Rejected:* each pass moving its own module, which leaves the code half-moved for weeks.
*Rejected:* doing it inside #282, which would bury the decisions in a mass rename.

**5. Everything lives in one package, `leaguebot`.** It is installed into the virtualenv
through a `pyproject.toml`, and the bot starts with `python -m leaguebot`. The tests then import
the bot exactly as it runs, from one place, where today the tests and the bot see the code from
two different roots; #398 was an import that worked under test and failed in the bot for that
reason. Module folder names like `results` or `image` also cannot clash with an installed
library of the same name.
*Rejected:* keeping each folder as a top-level package. *Rejected:* setting the path in the
start command instead of installing.

**6. The tests are grouped the same way**, one folder per module.
*Rejected:* leaving the tests in one flat folder.

**7. `season_cog.py` is broken up in stages.** First, the seasons being set up move off the
cog into a core service. Then the season review, the season approval and the round results
amendment move into their owners' services. Last, what is left is split by command group:
`season`, `division`, and `round` with `round results` under it.
*Rejected:* splitting by command group straight away, which moves the size without reducing
it. *Rejected:* leaving the file whole.

**8. Posting to Discord: one small handler for each kind of post.** There are four kinds:
log lines; posts the bot updates or replaces in place (calendar, lineup, standings, sheets,
forecasts); one-off notices; and channels the bot creates and later deletes whole. Each
handler holds the rules for its kind once: what to do when a post fails, who it may mention,
and remembering the message so it can be found again. Until the handlers exist, the router is
described as what it is: the log writer, and the poster of a forecast's text.
*Rejected:* one gateway for every post, which would need to know every module's rules.
*Rejected:* one handler per channel, which repeats the same rules a dozen times.

**9. A failed post is retried by its owner.** A post that fails inside a change is retried by
the change queue (decision 16), which runs the owning module's post again. The text is then
written at the moment it is sent, and the owner remembers the new message as usual. The old
retry queue is kept for log lines only.
*Rejected:* the retry queue re-sending its stored text and writing the new message's id back
itself. *Rejected:* no longer retrying these posts. *Rejected:* keeping the retry queue for posts
beside the change queue, as two mechanisms for one job (decided with decision 16).

**10. The rules are checked by import-linter and by our own tests.** import-linter checks
which code may import which. Our own tests check the rest. Where today's code breaks a rule,
each breach is listed with the issue that will fix it, and the list may only shrink (CLAUDE.md,
"Testing", says how to work with it).
*Rejected:* our own tests alone.

**11. Modules depend on core, never the other way round.** Core reaches a module only through
a few fixed hook points, which modules sign up to. Each module turns itself on and off. Which
module needs which is written down once, in one table.
*Rejected:* only writing the dependencies down as a table, which leaves each module's
switch-off code in core. *Rejected:* registering a module object for each module, more
machinery than a handful of modules need.

**12. Timed work and restarts work the same way across the whole bot**, in the shape
`steward_module.md` §3 already designs for stewarding. The database records when each thing is
due. A scheduled job only wakes the bot up, and the code it runs checks the database again.
After a restart, one sweep picks up the timed events that came due while the bot was down. A
change cut off part-way is finished by the change queue (decision 16). Existing modules move
over to this through tracked issues.
*Rejected:* using this shape for stewarding only, which leaves the bot with three different ways
of recovering: the start-up code's own steps, the results module's "results posted" flag, and
stewarding's own design. *Rejected:* reporting half-done work for a league manager to repair by
hand instead of finishing it. *Rejected:* a separate record of work still owed after a save,
beside the queue (decided with decision 16).

**13. Deletes are proven by a test, and the database deletes linked data only where it is
safe.** A test fills a database with one row in every table and runs every kind of delete
against it. After that, each link is changed to delete automatically only where the linked
data plainly belongs to what is being deleted, and never where the link is there to stop a
delete.
*Rejected:* letting the database delete linked data almost everywhere, which can quietly
remove history. *Rejected:* only writing down how each link should behave, which would never
run a delete.

**14. Each table is written by one module.** Other modules ask that module to make the
change. Each module's design file lists the tables it owns.
*Rejected:* also moving each module's columns off core's tables now.

**15. No ruff for now.** It may come later as an issue of its own.

**16. Every change goes through one queue.** Every change to the bot's data, whether a person
asks for it (a command, a button, a form, a typed answer) or a timer does, is put on one queue
and carried out one at a time. A change is made of steps, each saved as it finishes, so after a
restart the change carries on from its last finished step. Whoever asked hears back at once and
again when it is done, in words the core specification will set with the queue (#439). What can
be checked is checked before a change is queued. A step that fails because of Discord is
retried, without holding up other changes, and reported if it keeps failing. The queue writes
the audit record and the log-channel line for every change. Decided while reviewing #282's
issues, after carrying out changes on the spot turned out to cause thirteen of the defects found
(#439 lists them).
*Rejected:* putting only the league's championship changes, or only the long approvals, through
the queue, which leaves every other change with today's risks. *Rejected:* making whoever asked
wait for the result, which a long queue could stretch past the 15 minutes Discord allows for a
reply. *Rejected:* starting a cut-off change again from the top. *Rejected:* each command
writing its own audit record, which is how settings came to have none. *Rejected:* stopping a
change at its first failed step.

### Decided as usual good practice

These were not put to the owner as questions, being the usual practice. The owner approved them
with the plan for #282, or saw them in the summary of the architecture that followed it.

- Database code lives only in services, never in cogs or in the start-up code.
- Services may use Discord: they post and build the buttons they post. Keeping Discord out of
  them would need a separate posting layer, which a bot this size has no use for.
- The queue carries out one change at a time across the whole bot, since SQLite lets one
  writer in at a time anyway, and refuses a request for a change already waiting, running or
  done.
- While a save is open, the bot waits on nothing but that save's own connection (#155).
- Start-up work runs once, in one function that tests can run.
- Every kind of failure reaches the log channel in one standard way.
- A catch-all error handler always keeps the full error details for the host's log.
- Timed jobs are only created through the scheduler service, and each says what happens if
  it is missed while the bot is down.
- Anything the bot may later edit or delete, it remembers.
- Private helper code stays inside its own module.
- Core draws its own graphics through a hook, not by reaching into the image module.
- A database update is applied whole, or not at all.
- Kept as they are, and written down here: `report_failure` for commands, buttons and forms;
  the `LeagueCommandTree`, `LeagueView` and `LeagueModal` base classes; and one bot for one
  league on one server.
- import-linter runs as part of the normal test run (CLAUDE.md, "Testing").

---

## How the code is laid out

Everything is in one package, `leaguebot`, with one folder for each module (decisions 3 and 5):

```
leaguebot/
    core/         the shared plumbing, and core's own commands and rules
    results/
    attendance/
    signup/
    weather/
    image/
    steward/      once it is built
```

Each folder holds that part's **cogs** (its commands, and the buttons and forms they post), its
**services** (its rules and its database code) and its **models** (plain data). The tests
follow the same folders (decision 6).

Core holds what every module shares: the database connection and the migrations, the bot
object (`LeagueBot`) and the one function that builds its services, the scheduler service, the
base classes for the command tree, buttons and forms, `report_failure`, and the log channel
writer.

**What each part may do:**

- **A cog** turns a command, button or form into a call to one service, and replies. It holds
  no database code and no league rule. Every rule a league relies on can then be tested by
  calling a service, with no Discord interaction involved.
- **A service** holds the league's rules and all the database code. It may use discord.py:
  it can post to a channel and build the buttons it posts. It never imports a cog.
- **Buttons live beside the code that posts them:** with the cog when a command posts them,
  with the service when a service does. A service never reaches into a cog for a view.
- **Shared helpers** (today's `utils`) use no services, apart from the bot's type naming them
  (below). Once the code is grouped by module they are part of core, and the rule becomes core's
  own: core uses no module.
- **A model** is plain data. It uses no Discord and no database.
- **The database code in core** opens connections and applies migrations, and imports nothing
  else of the bot's.
- **The start-up code** builds the services, wires them together and starts the bot. It holds
  no rules and no database code of its own.

**Services are built in one place** (decision 1). The builder function makes every service,
passing each the other services it needs, and attaches it to the bot. A service is complete
when it is built: nothing is added to it later. A service that needs Discord itself (to post,
or to find a channel) is given the bot for that and nothing more. It does not use the bot to
look up other services. That lookup is what lets a hand-made fake bot in a test decide which
code runs (#240).

Because the services stay on the bot, the bot's type (`LeagueBot`) names every service of
every module. It does so for the type checker only, under `TYPE_CHECKING`, and nothing runs
because of it. That is the one place core names the modules' services, and it is the cost of
decision 1. `.importlinter` lists it as a stated exception rather than a breach.

---

## How modules and core fit together

**Modules depend on core, never the other way round** (decision 11). A module may use another
module only where the dependency table says so. The table is written once and read wherever
it matters: when a module is turned on, when one is turned off, and when a warning names what
will be switched off with it. It starts with one entry, attendance needing results (the
attendance module specification's opening rules), written today as if-branches in the module
cog. Stewarding will add a second [STW-MOD-007].

The image module is used by the others to draw their posts, and a module that finds it turned
off posts text instead, as the image module specification's "Configuration" section (the `images
config toggle` and its fallback) says. The image module draws what a module hands it, and
imports no other module. Every other module may call the image module without an entry in the
table; core reaches it through a hook, as below. Where a module must let one that depends on it
act (results after a round, for attendance), it does so through a hook too, as core does.

**Core reaches a module only through hooks.** Where something in core has to let the modules
act (a round amended or cancelled, placements confirmed, a review gathering its lines, a
season ending, a module turned off), core declares a hook. Each module signs up to it when the
bot starts. Core calls whoever signed up and never imports a module.

Each module still checks its own on/off switch where its own work starts, as it does today.
`tests/unit/test_attendance_module_gate.py` pins that for attendance. A hook does not replace
the switch. It only means core no longer needs to know the module is there.

**Core draws its own graphics through a hook too.** Core posts graphics of its own (the
calendar, the lineup). It asks for them through a hook the image module signs up to, rather
than importing the image module.

**Each module turns itself on and off.** A module's service has the code that turns it on and
off, next to the code that sets up what turning it off must remove. Keeping them apart is how a
module's switch-off came to leave things behind: signup set four permissions on its channel and
its switch-off cleared three (#372). The `/module` commands in core only handle the command
itself, the confirmation, and anything the dependency table says must go with it. What turning a
module on or off must do is set by the constitution's Principle X and by each module's
specification, not here.

**A module's private code stays inside it.** A name starting with an underscore is used only
inside its own module (PEP 8's convention, which fits exactly once each module is a package).
What a module offers the others is public, and its docstring says what it promises.

---

## The database

**All database code is in services**, or in core's database code for connections and
migrations. A cog never opens a connection, and neither does the start-up code.

**One step of a change is one save.** A step opens its connection, makes its changes, and
either commits them all or none. It does not hand its connection back to whoever called it. When
one module has to change another module's table in the same step, it calls that module's service
and passes its own connection down, so the step still happens all at once.

**The queue writes a change's audit record** in the same save as the step that makes the
change, and its log-channel line once the change is done, saying how it turned out (decision
16). The core specification's "The record of what changed" asks for both, and no command has to
remember them.

**While a save is open, the bot waits on nothing but that save's own connection** (#155). Not
on Discord, and not on a second connection. SQLite lets one writer in at a time, so a post made
in the middle of a save holds up every other save in the bot for as long as Discord takes to
answer. A second connection that tried to write would be kept waiting by the first until it
gave up and failed. Save first, then post, then record what was posted in a save of its own.

**Each table is written by one module** (decision 14). Every statement that changes a table
lives in the module that owns it, and other modules ask that module to make the change. Each
module's design file lists its tables. Where a module keeps its own columns on a core table,
as weather does on rounds and sessions, attendance on rounds, and results on a round's status,
the design file names those columns as an exception rather than moving them.

**Where a rule goes: the schema or a service.**

- A rule about identity or reference (a round belongs to one division; the bot holds at most
  one live season)
  goes in the schema, as a key.
- A fixed list of values lives in its Python enum. It is repeated as a CHECK in the schema
  only where the list is not expected to grow, and a test ties the two together, in
  `tests/unit/test_schema_rules.py`, which already ties the image defaults to their Python
  constants.
- A trigger only keeps tables of one module in step.
- Everything else is service code.

Once the bot is live, a CHECK, or a key declared inside a table, can only be changed by
rebuilding the table, which is why the rule is settled now, before stewarding adds its tables.

**Deletes are proven by a test** (decision 13). Deleting a season, division, round or driver
removes what hangs off it through lists written by hand. When a module added a table and a list
was not updated, the delete failed or left rows behind: #151 and #235. A test fills
a database with one row in every table, fails when a new table is not filled, and runs every
kind of delete against it. After that, a link may be changed to delete automatically, one at a
time, only where the linked rows plainly belong to what is being deleted. Never where the link
is there to stop a delete, as a placed driver's seat stops a team being removed from under them
(#148). A module's design file names any link of its own that is there for that reason.

**A database update is applied whole, or not at all.** Each migration runs in one transaction
with foreign keys switched off, and the keys are checked before it commits. This is SQLite's
own procedure for changing a table ("Making Other Kinds Of Table Schema Changes", in its ALTER
TABLE documentation). With the keys switched on, rebuilding a table would silently delete the
rows linked to it by a cascade, empty the links set to be emptied, and fail on the rest. The
rules for the schema baseline itself are CLAUDE.md's.

**What going live changes for the schema is listed in one place,** the `run_migrations`
docstring, so nothing is missed on the day.

**A column nothing reads is removed, not kept,** as #436 did for whole tables. A stored value
nobody reads still has to be written, and goes stale without anyone noticing.

---

## How a change is carried out

Every change to the bot's data goes through one queue (decision 16). Commands that only read do
not.

- **A person asks, or a timer fires.** Either way the change is put on the queue. What the
  person who asked is told, and when, is the core specification's, which gains it with the queue
  (#439, "What a league sees").
- **Checked before it is queued.** Whatever can be checked at the start is checked (the channel
  exists, the bot may post there, the text fits, the change is allowed in the season's current
  stage), and the request is refused if not. That is the gate `steward_module.md` §4 designs for
  a cycle's close, made bot-wide.
- **One change at a time.** One worker runs one change at a time, across the whole bot, taking
  them in the order they were asked for. Two presses of the same button can no longer run into
  each other, and a request for a change already waiting, running or done is refused.
- **Steps, each saved.** A change is made of steps: save this, post that, save the next. Each step
  is recorded as done when it finishes. After a restart, the worker carries on from the last
  finished step, and nothing already done is done again.
- **Posts are remembered.** A post a step makes is recorded with its message id, so repeating the
  step replaces the post rather than adding a second. The one gap: if the bot stops after a post
  is sent but before it is recorded, repeating the step sends it again, and a post that replaced
  an earlier one leaves its first copy behind. Scanning the channel to spot that is not done:
  `steward_module.md` §7 rejects scanning a channel as guesswork, and that reasoning carries
  over.
- **A failed step is retried, without holding up the queue.** A step that fails because of
  Discord is retried with growing waits, by running the owner's post again, as text where it
  would have been a picture (Constitution XIV, rule 8). While it waits, it steps aside: the worker
  runs other changes, and only a later change to the same round or division waits behind it. It
  is retried
  until it succeeds, and reported to the log channel if it keeps failing, as the core
  specification's "When the bot stops" requires of a failed post. The old retry queue is kept
  for log lines only.
- **The record is the queue's.** The queue writes the audit record and the log-channel line for
  every change.

---

## Timed work and restarts

The bot is mostly driven by the clock: weather phases, check-in calls and deadlines, results
channels opening, clean-ups. It also has to carry on correctly after it has been stopped. The
whole bot does this in the shape `steward_module.md` §3 already designs for stewarding (decision
12). Where stewarding's own design keeps a record the queue now keeps (§4's close states, §7's
message table), bringing it into line is #289's.

**The database says when something is due.** A timed event is a row with the moment it is
due. The scheduled job (APScheduler) only wakes the bot at that moment. The code it runs reads
the row again and acts only if the row still says the work is due. A job that fires after its
work was changed or cancelled then does nothing. The job store becomes a convenience: if it
were lost, the bot could rebuild every job from the database.

**Each kind of job says what happens if it is missed.** When the bot was down at the moment a
job was due, the job either runs late or is skipped. That choice is declared once for each kind
of job, where the kind is registered, and the start-up sweep applies it. Every job is armed with
no limit on how late it may run (`misfire_grace_time=None`), so the scheduler never drops one on
its own and its default never decides, as `steward_module.md` §3 sets out. Which choice is right
is a rule a league notices, so it belongs to the core specification ("When the bot stops").

**Jobs are made only through the scheduler service.** Nothing else touches APScheduler
directly. A round's job is named from the round and the event, so arming it again replaces the
old one instead of adding a second (the `_round_job_suffix` docstring in the scheduler
service). One function works out when each of a round's events
falls, and one function arms them. Every path that arms a round (a season approved, a round
amended, test mode) uses those two, so the timings cannot drift apart between copies again.

**Start-up runs once, in one function.** discord.py can report the bot as "ready" more than once
in a single run, after a network drop (#446), so start-up work does not belong in the handler
for that event. Start-up:

1. reads the settings (the token, the database path), in `main` rather than on import;
2. applies the migrations, before connecting to Discord;
3. builds the services with the one builder;
4. runs the start-up sweep once, when Discord first connects;
5. starts the queue, which carries on with any change a stop cut off;
6. only then lets scheduled jobs run.

Tests can then run the whole start-up in order, instead of checking its order by searching the
source code.

**One sweep picks up the timed events that were missed.** It walks everything that came due
while the bot was down, in the order it would have happened, and applies each kind's declared
choice: run it late, or skip it. A timed event that makes a change puts it on the queue, like
any other. Each step of the sweep is kept separate, so one failing is reported and does not stop
the rest.

**A change cut off by a stop is the queue's to finish,** not the sweep's (see "How a change is
carried out"). Approving a season, for example, is one change: its lineups, calendars and sheets
are its later steps, and a restart carries on with them.

**Background work is started through one helper** that keeps hold of it. Python only keeps a
weak hold on a task nobody stores, so such a task can vanish part-way, and its failure goes
unseen.

**Nothing blocks the bot.** Slow work (drawing a graphic, checking the templates, reading a large
file) runs off the bot's main loop, so commands and buttons keep being answered while it runs.

**The time is always passed in.** A service that needs the current time takes a `now` argument,
and every time inside the bot is in UTC. How tests treat `now` is CLAUDE.md's.

---

## Posting to Discord

**One small handler for each kind of post** (decision 8). The posts the bot makes fall into
four kinds, and each kind has its own rules for failures and for later changes:

| Kind | Examples | What its handler looks after |
|---|---|---|
| **Log line** | configuration changes, outcomes, failures | posting to the log channel, and retrying a failed line; what a log line may hold (a mention names without notifying; a long record is split) is the core specification's "The record of what changed" |
| **Standing post** | calendar, lineup, standings, attendance sheet, forecast | remembering every message it posts, replacing the previous one, and on failure having the owner post it again |
| **Notice** | a cancellation notice, a verdict | remembering the message wherever it might later be edited or deleted |
| **Bot-owned channel** | a results submission channel, a driver's own signup channel | posts inside a channel the bot creates and later deletes whole; only a post the bot must find again, such as a prompt it replaces, is remembered |

The code that makes a post still builds its own text, pictures and buttons. The handler looks
after everything that happens once it is sent.

**Every post keeps three things,** whichever handler sends it:

- **Its message id,** wherever the bot might later edit, delete or replace it. The id is saved
  against its owner's record at the time. A message nobody recorded can never be found again,
  which is what #189 was.
- **Who it may mention,** stated where it is sent.
- **What happens if it fails:** either it is retried, or the failure is named in the log
  channel.

**A failed post is retried by its owner** (decision 9). A post that fails inside a change is
retried by the change queue, which runs the owning module's post again, as text. The text is
then written at the moment it is finally sent, and the owner remembers the new message as it
always does. No queue ever holds a picture; the constitution's Principle XIV, rule 8, says why.
The old retry queue is kept for log lines.

Which channels exist, and what may go in each, is the constitution's Principle VII and the
channel registry (`channel_registry_service`).

**Today** only the log channel and a forecast's text go through the router
(`utils/output_router.py`), and it is the only code that writes to the log channel, though a
failed log line is re-sent later by the retry worker. Everything else is posted directly by the
module concerned.

---

## Errors and failures

**Each kind of starting point has one failure path.** Whatever goes wrong, the full error goes
to the host's log and one line goes to the log channel, and reporting it never raises another
error:

- **Changes**, from whatever starting point, are reported by the queue: the outcome to whoever
  asked, and a line to the log channel (decision 16).
- **Commands, buttons and forms** go through `report_failure`
  (`utils/interaction_errors.py`), called by the `LeagueCommandTree`, `LeagueView` and
  `LeagueModal` base classes. What the member and the log channel are told is the core
  specification's "When a command fails".
- **Scheduled jobs and repeating loops** go through one job runner.
- **Discord events** (a message, a member leaving) go through one error handler on the bot.
- **Background tasks** go through the helper that starts them.

**No cog handles its own errors** (decision 2). The command tree steps aside for any command or
cog that has its own error handler (`LeagueCommandTree.on_error` in `utils/league_server.py`),
so one added handler would quietly switch `report_failure` off for everything it covers.

**A catch-all error handler** (`except Exception`) is allowed in only four places:

1. on one of the failure paths above;
2. where the bot works through a list (divisions, drivers, posts) and one item failing must not
   stop the rest. The failure becomes a line in the result, which the command reports to the
   manager (the pattern #237 adopted), and its log line says the job did not complete, as the
   results module specification's "A republication that does not land shall be reported"
   requires. This does not apply where an operation's own rule is all or nothing, as a points
   amendment's is (the results specification's "Changing points system mid-season");
3. around reporting a failure, where the report itself might fail;
4. around a clean-up that then raises the error again.

In every case it keeps the full error details for the host's log. A command catches only the
errors it expects by name, and only to turn them into a refusal the member can act on.

---

## One bot, one league, one server

The bot serves one league on one Discord server. `utils/league_server.py` explains how, in its
docstring (#244, #247). In short: the server is checked once, where a command, button, form or
event first arrives, and nowhere after. Below that, nothing in the bot is told which server it
is on. The tables carry no server id except the one settings row, and the services take none,
because the database only ever holds the one league. `tests/unit/test_one_league_server.py`
and `test_no_server_id_is_left_outside_server_configs` in `tests/unit/test_schema_rules.py`
hold this in place.

The owner-only `!sync` command in the start-up code is the one way in that skips the check. It
only refreshes Discord's list of commands and touches no league data.

---

## Where the other rules already live

These rules bind every module but are written down elsewhere. They are linked here, not copied,
so they cannot drift apart:

- **The base classes** for commands, buttons and forms: `LeagueCommandTree`, `LeagueView` and
  `LeagueModal`, in `utils/league_server.py`. `tests/unit/test_one_league_server.py` checks
  that no view or form derives from discord.py's own classes directly.
- **The failure path for commands, buttons and forms**: `report_failure`, in
  `utils/interaction_errors.py`.
- **The coverage floor for each module, and the single schema baseline until go-live**:
  CLAUDE.md, under "Testing". The values are in the CI workflow and the `run_migrations`
  docstring.
- **The type check**: CLAUDE.md and `mypy.ini`.
- **Staging files by name, and moving files with `git mv`**: CLAUDE.md, under "Working
  conventions".

---

## How the rules are checked

Where a rule can be checked by reading the code, a test checks it and fails the build
(decision 10). The rules about how things are built (the hooks, the one builder, start-up, the
sweep) cannot be checked that way until they exist; "Known divergences" below lists what is
not checked yet. The checks are:

- **`.importlinter`**, run by `tests/unit/test_import_contracts.py`, checks which code may
  import which.
- **`tests/unit/test_architecture_rules.py`** checks the rest: database code outside services,
  awaiting anything but the connection mid-save, cogs handling their own errors, catch-all
  handlers that lose the error details, background tasks nobody keeps, code reaching past the
  scheduler service, jobs armed with a lateness limit, private names used across modules, a new
  place naming the log channel (the check lists the six allowed, with their reasons), and
  posting outside the handlers.
- **`tests/unit/test_one_league_server.py`**, **`test_schema_rules.py`** and
  **`test_import_roots.py`** check the one-league rule, the schema's own rules, and that nothing
  imports the bot through a package named `src` (#398).

**Where today's code breaks a rule, the breach is listed in the check** with the issue that
will fix it, and the list can only get shorter. How to work with the lists is CLAUDE.md's, under
"Testing".

Once the queue exists, a further check holds that nothing writes to the database outside a
queued change (#439).

---

## Known divergences

**The code does not match this file yet.** Below is everything #282's review found to differ,
how much of it there is, and the issue that will fix it. Where a check counts it, the number is
the check's, measured on 25 September 2026, and the check's own list is the up-to-date one.
Lines leave this table as the issues close.

### Counted by a check

| What does not match yet | How much today | Fixed by |
|---|---|---|
| Code below the cogs imports a cog | 7 import links (11 import statements) in 5 services | #283, #285, #286 |
| A utility imports a service (the log writer queues its own retries) | 1 | #441 |
| Database code outside services | 176 calls in 68 functions (the cogs and the start-up code) | #283, #284, #285, #286 |
| Something else awaited while a save is open | 1 (test mode's fake drivers) | #283 |
| Catch-all handlers that lose the error details | 125 in 109 functions | #442 |
| Background tasks started and dropped | 5 | #453 |
| Code reaching past the scheduler service | 22 places in 13 functions | #283, #286 |
| Jobs armed with a lateness limit (the scheduler's default grace) | 13 | #283, #286 |
| Private names used across modules | 28 uses of 21 names; names read through an object are not seen, so there are more | #284, #285, #286, #288 |
| Posts made directly rather than through a handler | 128 in 61 functions | #441 (core's), then each module's pass |

### Not counted by a check

| What does not match yet | Fixed by |
|---|---|
| The code is grouped by layer, not by module, and is not one installed package | #438 |
| The rules between modules (core never imports a module, tables written by one module) have no check until the code is grouped by module | #438 |
| Services are built one by one in the start-up code, and the signup wizard is wired in afterwards | #283, #286 |
| The settings are read when the start-up file is imported | #283 |
| Start-up runs in the "ready" handler, which can run again after a network drop, and one failing step skips the rest | #283, #446, #447 |
| Migrations run after Discord connects, and the scheduler starts before any recovery | #283 |
| Restart recovery for five modules is written into the start-up code | #283, then each module's pass |
| Core calls into every module directly; there are no hooks | #283, then each module's pass |
| Every module's switch-on and switch-off is in core's module cog, and the one dependency is written as if-branches there, not a table | #283, #286 |
| Seasons being set up are held on the season cog and found by its class name | #283 |
| `season_cog.py` holds three whole workflows | #283, #284 |
| League rules written in cogs (attendance's thresholds and check-in rules, the reserves toggle) | #285, #284 |
| Changes run on the spot, inside the command, button or timer that asked for them, not through one queue | #439, then each module's pass |
| Settings commands write their audit record in a second save, and some write none | #439 |
| A round's timed work is armed, timed and caught up in separate places | #283, #285, #440 |
| A change cut off by a stop is left half-done | #439 |
| The retry queue carries forecasts, calendars and sheets as well as log lines, and forgets what it delivered | #439, #441 |
| Timed jobs, events and background tasks have no failure path | #439, #453 |
| Some commands answer their own failures, naming the error | #454 |
| Cancelling a job treats every error as "no such job" | #283, #286 |
| Deletes are not proven by a test | #283 |
| Migrations are not applied whole with the foreign keys checked | #283 |
| No test ties the schema's fixed lists to their Python enums | #283 |
| The go-live obligations for the schema are scattered | #283 |
| Three standings columns (`current_position`, `current_points`, `points_gap_to_first` on `driver_season_assignments`) are stored and never read | #283 |
| Penalty and appeal reviews resume only their prompt after a restart | #284 |
| Two docstrings say a verdict's message id is never stored | #284 |
| Services read the clock themselves in about fifty places, and one reads the local date rather than UTC | #283, then each module's pass |
| Tables are written by more than one module: by a rough count of the SQL naming each table, the audit table by four and `divisions` by three | #438 (the check), then each module's pass |
| Modules import one another outside the dependency table: image reads results, attendance and weather; results calls attendance; results and signup call each other; signup calls weather | #438 (the check), then each module's pass |
| A failed log line is re-sent by the retry worker, not by the log writer, and without its setting that stops a line notifying anyone | #441 |
| Not yet checked or counted: catch-all handlers outside the four places, errors caught other than by name, a connection handed back to its caller | #283–#288 |
| Checking the templates runs on the bot's main loop, holding everything up each time a graphic is prepared | #288 |
