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

*Rejected:* grouping by layer first (`services/results/`, `cogs/results/` and so on), which
spreads one module over four folders. *Rejected:* grouping only the services. *Rejected:* staying
flat with a list of which file belongs where, which is the arrangement that misfiled core's
driver and team code under signup. *Rejected:* leaving the tests in one flat folder.

Inside each folder, each kind of code has a folder of its own: `cogs/`, `services/`, `models/`,
and `utils/` for the part's helpers, with `db/` in core for the database code. What kind of code a
file holds is then plain from where it sits as well, so a rule about one kind (no service imports
a cog) is written once for every module, and the checks tell a service from a cog by its folder.

*Rejected:* a module's files side by side in its folder, where a file's kind is known only from
its name, and several files are named for neither.

Core holds what every module shares: the database connection and the migrations, the bot object
(`LeagueBot`), the change queue, the scheduler service and the start-up sweep, the base classes
for the command tree, buttons and forms, the failure paths (`report_failure`, the job runner, the
bot's error handler for events, `bounded_autocomplete`, the helper that starts background work), and
the handlers for each kind of post: the log writer, the notice handler, the standing-post handler
and the bot-owned channel handler, each a service of its own in `core/services/`.

**The entry point** (`__main__.py`) sits above core and the modules, and is the one place that
imports every module at run time. It holds the builder, which makes every service, loads every
module's cogs, signs each module up to core's hooks and registers each kind of timed job with the
scheduler service, all before the bot connects. It then starts the bot. That is how core can offer
hooks without importing a module.

**What each part may do:**

- **A cog** turns a command, button or form into a call to one service, and replies, unless it
  hands a button's press to a service's workflow, which answers through the interaction. It holds no
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
  post to a channel and build the buttons it posts, and a workflow, to which a cog hands a button's
  press, takes the pressed interaction and answers the member through it, while the gates it walks
  stay callable with no interaction (`approval_checks`, and the stage and in-hand checks the change
  judges again in its own check). Keeping Discord out of the services entirely would need a
  separate posting layer, which a bot this size has no use for. A service never imports a cog.
- **Buttons live beside the code that posts them:** with the cog when a command posts them, with
  the service when a service does. A service never reaches into a cog for a view.
- **Shared helpers in core** are of two kinds. Those used by cogs (the base classes, the guards,
  autocomplete) reach core's own services through the bot, as a cog does. Plain helpers
  (formatting, parsing, time) use no service at all. Neither imports a service, and core uses no
  module; the one exception to both is the bot's type, which names every service for the type
  checker (below). Each handler for a kind of post is a service in core, not a helper. One helper a
  service may call reaches a core service through the bot: `league_guild(bot)`
  (`core/utils/league_server.py`), the one route from a job or a service to the league's server,
  which reads the configuration service to find it. Handing the configuration service instead to
  every service that posts would add a parameter to each, for no rule gained.
- **A module's own helpers** sit in its `utils/` folder, beside its services, under the same rules.
- **A model** is plain data. It uses no Discord and no database, and no service or cog.
- **The database code in core** opens connections, lends them again, closes them and applies
  migrations, and imports nothing else of the bot's.
- **The entry point** builds the services, wires them together and starts the bot, and calls core's
  database code to close the connections as the bot stops. It holds no rules and no database code
  of its own.

**Services stay attached to the bot, and are built in one place.** The bot object keeps a typed
attribute for each service object. Most services are plain functions and need none. The builder
makes every service object, handing each the other services it needs, and a service is complete
once it is built: nothing is wired into it after the bot starts. A test can call the builder to
get the real set of services. A service that needs Discord itself, to post or to find a channel,
is given the bot for that and nothing more. It does not use the bot to look up other services,
save through `league_guild` (above), and a service that is a plain function is handed the services
it needs by whoever calls it. Looking services up on the bot is what lets a hand-made fake bot in a
test decide which code runs.

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

**Core reaches a module only through hooks, and the image module through a protocol it declares
(below).** Where something in core has to let the modules act (the bot starting, a timed event
falling due, a round amended or its timed work armed again, a round's timing windows read, a round,
a division or a season cancelled, a round's results made final, placements confirmed, a season's
configuration confirmed, a review gathering its lines, the placements review gathering each
division's lines, a division's channel faults read, a driver's name read, a season's fingerprint
taken, a season's confirmation judged, a season ending, a season, division, round or driver
deleted, a driver merged into another or turned down, a driver's account changed, a base role
changed, the bot packed, test mode turned on, the bot's in-memory state cleared, the hub's panel
gathering its options, a module's switch read), core declares a hook. The builder signs each module
up to it before the bot connects. Core calls whoever signed up, and never imports a module, the
bot's type aside (see "How the code is laid out"). A module turned on or off is not a hook but a
change of the module's own, which core asks for by its kind (see "Each module turns itself on and
off").

**A hook core declares is a list of subscribers.** Core declares the moment and what it hands over:
what the moment concerns, where it concerns anything (the rows, the round), and, where core holds
one open, the connection its subscribers read and write on (the save's own where the moment falls
inside one, so that what they write is part of the save). Each module signs up a function of its
own, and core calls each in the order they signed up. A subscriber may hand something back, which
the hook gathers in that order; what core does with them, if anything, is the moment's: a value to
read or to keep, a line to write, a step to plan or to register, a refusal to give. The hook names
no module and no module's operation, so a module's part of the moment is the module's own code, and
a module added later signs up without core changing, the switch read aside (below). A hook keyed by
a kind takes one function for each key instead, as a kind of timed job takes its one handler (see
"Timed work and restarts"). **A module's switch read is a keyed hook:** one reader for each module,
keyed by the module's name, which the module's own work may also call; core asks for it by that name
(`is_<module>_enabled`, or `is_enabled_on` on a connection the caller holds), because core names the
modules and shows which are on in the reviews (the core specification's "Modules"), and keeps the
dependency table (above). The key is the name the module's switch is asked by: `images` for the
image module, the module's own name for the rest. So a module added later adds its
`is_<module>_enabled` to core's module service as well as signing up; core knows its name for its
switch and nothing more. A module that never signed up is a fault in the bot's assembly, and asking
for it raises `SwitchNotSignedUp`, a `LookupError` of its own type, which no reader that stands
aside takes for an answer: each lets that type through by name and stands aside from any other
fault. A link the dependency table allows between two modules is not one of core's hooks: there the
module depended on may name what it asks of its dependant, as results does of attendance (below).

*Rejected:* a hook as one object of named operations the builder fills in, one for each module's
part, which leaves core knowing what each module does at that moment. *Rejected:* only writing the
dependencies down as a table, which leaves each module's switch-off code in core. *Rejected:*
registering a module object for each module, more machinery than a handful of modules need.

Each module still checks its own on/off switch where its own work starts.
`tests/attendance/test_attendance_module_gate.py` pins that for attendance. A hook does not replace
the switch. It only means core no longer needs to know what the module does: for its switch, core
knows the module's name, and nothing else.

The image module is used by the others to draw their posts, and a module that finds it turned off
posts text instead, as the image module specification's "Configuration" section (the
`images config toggle` and its fallback) says. The image module draws what a module hands it, and
imports no other module. Every other module may call the image module without an entry in the
table. Core reaches it through a protocol it declares, which the image module implements and the
builder hands in, as results reaches attendance (below). Core's own graphics (the calendar, the
lineup) are asked for through it, with the capacity guards, the artwork signature and the checks
that a calendar or a lineup is wanted, and both season reviews' image section is asked for through
it too, with the faults that refuse a confirmation or an approval (the rasteriser, the templates,
the per-tier colours, the portrait settings), rather than drawn or read by importing the module. That is not the
rejected object of named operations above: a hook is a moment several modules take part in, and
this is a question one module answers. Where a module must let one that depends on it act (results
after a round, for attendance), it does so through what the builder hands it, never by importing
the dependant.

**Results reaches attendance through a protocol, handed in by the builder.** What attendance does
after a round's review (recording the round's attendance and its pardons, recalculating after an
amendment, posting the sheet, finding the drivers over a threshold, saying which pardons an
amendment's review may offer, checking, saving, changing the roles of and announcing one driver's
sanction, redrawing a lineup) is a protocol results declares, `AttendanceAfterReview`, and
attendance implements it on its own functions. The builder makes it once, attaches it to the bot and
hands it to the review's change types, so results imports nothing of attendance and the
`.importlinter` exception for it is gone. Of its methods, `enabled` reports attendance's switch,
`sync_hint` only words the hint naming the "attendance sync" command and reads no switch, and
`change_sanction_roles` acts whatever the switch says, as the attendance specification's "Sanctions
that do not apply" requires. Every other method checks the switch itself, on the connection it is
handed where it writes, and does nothing while the module is off. A change type never reads the
switch itself; one whose job serves attendance alone asks `enabled`. The placement service, which a
sanction moves seats through, is handed to attendance's implementation by the builder and not looked
up on the bot.

*Rejected:* results calling attendance's functions directly and checking the switch itself, which
is the dependency the table forbids and leaves each caller to remember the switch.

**Each module turns itself on and off.** A module's service has the code that turns it on and off,
next to the code that sets up what turning it off must remove. Kept apart, a switch-off comes to
miss what setup added, as signup's switch-off came to leave behind the permission its setup gives
the league admin role on its channel. The `/module` commands in core only handle the command itself,
the confirmation, and anything the dependency table says must go with it. What turning a module on
or off must do is set by the constitution's Principle X and by each module's specification, not
here.

A module's switch-on and switch-off are changes of its own, of the kinds `module.on:<name>` and
`module.off:<name>`, which core's `/module` command asks for by kind. One that only saves the
module's settings, with work after it that nothing waits on (the removal of its timed jobs), is a
`Setting` (see "The record is the queue's"). One that posts or removes on Discord is a change type
of its own. Where the dependency table makes one module's switch-off take another's with it, the
dependent module offers its switch-off as a function on the connection it is handed, and the builder
hands it to the change that cascades, as results' switch-off is handed attendance's.

**A module's private code stays inside it.** A name starting with an underscore is used only
inside its own module (after PEP 8's convention, extended to the files of one module). What a
module offers the others is public, and its docstring says what it promises.

---

## How a change is carried out

**Every change goes through one queue.** Every change to the bot's data is put on one queue, one
durable list of jobs, and carried out in order within its lane, save the one overtake (below),
whoever or whatever asks for it: a person (a command, a button, a form, a typed answer), a timer, a
Discord event (a member leaving), or a handler or start step the start-up sweep calls. Commands
that only read do not use it. Carrying out changes on the spot is what left data half-changed
when two presses overlapped, the bot stopped part-way or a step failed, and what let a success be
reported over a change only half done.

*Rejected:* putting only the championship changes, or only the long approvals, through the queue,
which leaves every other change with the same risks. *Rejected:* each command writing its own
audit record, which is how some settings came to have none.

Beside the stop notice's presses and a restore's swap at start-up (both below), two changes stay off
the queue, each a stated exception. `/bot pack` is refused while any lane holds a job (the core
specification's "Packing the bot for another server"), so it cannot be a job itself, and it saves in
one transaction (`pack_service`'s docstring). The factory reset's wipe puts a freshly migrated
database in place of the one the queue lives in (`wipe` in `factory_reset_service`); its clean-up of
Discord is an exception of its own (see "Posting to Discord", and `clean_discord`'s docstring).

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
  ahead of it may have moved the season on. A request a person made that fails is refused, and the queue goes on.
  A change type may ask, for a member's change its check refuses as it starts, a change of its own
  in the refusal's save (`ChangeType.on_refused`), as a step asks a follow-on: a season's
  approval and a mid-season placements confirmation use it to tell the review's channel of a
  refusal its member can no longer be told of. It may likewise ask one where a league admin
  discards a member's change stopped at its check before any job of it started
  (`ChangeType.on_discarded`), saved with the Discard. A refusal
  callback that raises stops its lane at the change's first job, as a check that raises does (save
  in a member's settings change or `/bot init`, described below), and the change is checked again
  at each try; a discard callback that raises is logged on the host and the Discard goes ahead
  without it, Discard being the queue's last way past a stop. *Rejected:* planning a step on the
  refused change, which would leave a refused change with jobs to run.
  A change a timer, an event, or a handler or start step the sweep calls asked for is dropped once
  its work is no longer due, as a job that fires after its work was cancelled does nothing (see
  "Timed work and restarts"). Where it is refused by its check for any reason but that its work is
  no longer due (a channel unset, the bot's permission lost), it has nobody to refuse, so it stops
  its lane like a job that fails (below), at its first job and with the check's reason, and is
  checked again at each try. So does any change, a member's included, whose check raises as it
  starts, save a member's settings change or `/bot init`: a change type that gives
  `ChangeType.on_check_fault` has such a change ended in one save as a failure and the lane goes
  on (see "A job that fails stops its lane"), while a bot's change of the same type still stops
  its lane. Meanwhile the log channel records what failed, as the core specification's "Setting
  the bot up" has it do. This `steward_module.md` §4 designs for a cycle's close, made bot-wide.

  A check that must refuse a press while the same approval is already in hand reads the queue
  (`unfinished`, or `in_hand` where the refusal names the job it waits on), which give the changes
  of the kinds asked for that are queued, running or stopped on a failure, leaving out the one
  whose check is running (`CheckContext.change_id`).
  What a change type refuses is read from the database and the queue, never from a flag kept in
  memory, so it holds across a restart. A bot's request that this check finds no longer due (a
  review reopened for a round that has since gone final) is dropped, not stopped: only a channel
  missing or unusable stops its lane.
- **All or nothing in one step.** Where a change must be all or nothing (as the results
  specification's "Changing points system mid-season" requires of an approval), everything it
  saves is saved in one step behind the gate, and only its posts come after, as
  `steward_module.md` §4 does for the cycle close. A round's approval is one such step: the
  penalties or corrections, the recalculated points, the round's attendance, the standings
  snapshots and the round's new status are saved together, and a fault in any of them rolls the
  whole step back and stops its lane there (results specification, stage one: what the commit
  writes shall be saved together).
- **One change at a time to a lane.** The queue is divided into lanes: the *league lane* and a
  *conversation lane* for each channel the bot opens for a conversation, keyed by that channel
  (`"channel:<id>"`). Which lane carries what is the core specification's ("How a change is
  carried out"); a change type gives its lane from the payload (`ChangeType.lane`), so the same
  request always lands on the same lane, and a follow-on takes its own kind's lane, not its
  asker's. A conversation lane writes only its conversation's own tables, held at run time (see
  "The database"): the sessions a round's submission collects are its own until its review opens,
  and so are a resubmission's drafts, while the swap of the round's results is the league's. What
  a conversation hands the league it asks of the league lane as a follow-on, which waits there in
  order with the league's other changes. League work a conversation handed over posts back into
  that conversation's channel by asking a change of the conversation's lane, so that a broken
  channel stops only that conversation. Nothing on a lane overtakes the change at its head, not
  even one whose job is stopped, save a change of a kind marked `ChangeType.overtakes_a_stop`
  (below, "A job that fails stops its lane"): `_choose` takes, of each lane, its running change or
  else its oldest queued one, or such an overtaking change queued, or already running past the
  stop, behind a stopped head not yet due for a try.
  Lanes run side by side, `ALWAYS_RUN_LANES` (the league lane alone) always and at most three
  conversation lanes with them, the rest waiting oldest change first. SQLite lets one writer in at a
  time, so one write lock, taken round each save and released at its commit, keeps that single
  writer in the process; no call to Discord is made under it, so a long save makes another lane's
  save wait in Python rather than fail on SQLite's busy timeout. Inside the save of a save step, or
  of a step with a record, and under the lock, the job is asked again whether it is still due and
  whether its conversation still stands (`conversation_open`), since another lane's save may have
  landed between the check and the save. The tests run the lanes one at a time, taking the runnable
  lane head with the lowest change number, so a test's outcome does not depend on timing. A
  change's posts hold its lane's worker while they are sent, so a long run of posts delays the
  changes behind it on that lane; that is the price of one change at a time to a lane. Job numbers
  are one sequence across the lanes, so that "job #N" names one job anywhere. The
  specifications call the league lane "the queue" and a conversation lane "a conversation's lane".
- **A conversation ends by the league's hand or its own, and its waiting turns are then dropped.**
  Which changes end a conversation, and the exception a round's cancellation makes for the pastes
  waiting in its submission, are the core specification's ("How a change is carried out"). A change
  that ends a conversation marks it ended in its own save and does nothing else to the lane. Each
  change of that lane is then found no longer due when its turn comes (`conversation_open`), and
  dropped with a line saying so; a closing change asked back by the league needs only its channel to
  stand. A cancellation marks the submission closed and leaves its row, so `conversation_open` still
  holds and each waiting paste reaches its turn, where its judging job refuses it; the conversation
  ends when its channel's deletion removes the row. A job of that lane already stopped stays
  stopped, with its notice, and the bot makes no try of its own on it (`_choose` clears its
  `next_try_at`): a Retry runs a try that finds it no longer due and clears the stop, a Discard
  drops it (unless the job cannot be discarded). A refused deletion of the conversation's channel
  stops the lane that asked: a conversation ending itself stops its own, a league change ending it
  stops the league.
- **The same change is not queued twice in a row.** A change is named by what it does, what it acts
  on and the values it sets (approving a round's appeals, reposting a division's calendar, setting a
  division's channel to a given one), and that name is its key. A request's key is compared with the
  last change saved on its own lane, whatever became of it, and the request is turned away as a repeat where they
  match and that change is still waiting to be taken up: a member's request is refused, a bot's
  dropped (the core specification's "How a change is carried out" holds what a member is told, and
  when a request is queued again). A change of a kind that may be repeated, such as a repost, is
  never turned away as a repeat, since it reads what it posts when it runs. A change that can only
  be done once, such as approving a round's appeals, is refused by the checks once it has been done,
  since the round has moved on. The rule turns a repeat away only while the first has not started;
  a change that must also be refused while the first is under way, or stopped, asks the queue
  (`unfinished` or `in_hand`) in its own check.
- **Steps, each saved with its mark.** A change is made of steps, and each step is a job with a
  number of its own, which every line about the job carries (the core specification's "How a
  change is carried out" says it is never reused; the database issues the numbers, and a
  restore and a factory reset, which put another database in place of the live one, write the
  live one's highest number into it first). A member's request is
  acknowledged once, though it is made of jobs that run one after another. The worker opens each saving
  step's connection and hands it down, so the step's changes, the change's audit record where it
  has one, and the mark saying the step is done all commit together or not at all. After a
  restart, the worker carries on from the first step not marked done, and nothing marked done is
  done again.

  *Rejected:* starting a cut-off change again from the top.
- **A restored state brings back no queue.** Restoring a saved state (the core specification's
  "Saving a state and returning to it") empties the queue that comes back with it, so a change under
  way when the state was saved is not resumed.
- **Posts are remembered.** A posting step sends the post, then saves its message id with the step's
  mark: a step that is not a save may carry a `record`, which the worker runs on the connection of
  the save that marks the step done, before the mark, so the id and the mark commit together or
  not at all. A `record` never commits, is refused on a save step (which writes in its own run),
  and runs also where a deletion or an edit finds its message already gone, so that a row tied to
  that message is still closed. *Rejected:* a save step after each post to write its id, which
  doubles the jobs a review numbers and stops on, for no gain. Where the post replaces an earlier message, deleting that message is a step of its own,
  after it, reading the old message's id, which the posting step saved beside the new one, so the
  new post stands before the old one goes (Constitution XIV rule 8). Three windows remain. If the bot
  stops after a post is sent but before its id is saved, the step runs again after the restart and
  sends a second copy, and the first copy is left behind. The old and new messages stand
  together between a post and the deletion after it: until the change finishes on restart, if the
  bot stops in between, or for as long as the deletion's stop lasts (below). And a conversation's
  job may send its message just as the league ends that conversation: the job's save then finds
  the conversation no longer due and records nothing, so the message stands untracked until the
  ended conversation's channel is deleted. That one arises only in a conversation's own channel,
  which the ending takes down. The design accepts all three. Constitution XIV rule 8 ("at most one such message stands at any
  moment") is amended with the queue to allow the first two; the third is a conversation's post,
  which no replacement rule covers, and is accepted by the design alone. A `record` that raises is not a stop of the bot: its save is rolled back, its lane stops at
  the job like any failure, and what the step returned is kept on the job (`StepContext.kept`,
  which a step's next try reads, and which the first try finds empty), so the next try removes
  the copy already sent before it posts again. A post of several messages that fails part-way
  keeps the ids already sent the same way. Scanning the channel to spot such a copy is not done: `steward_module.md` §7
  rejects scanning a channel as guesswork, and that reasoning carries over.

  A deletion that fails stops its lane like any other job (below). A deletion that follows a post
  which was discarded deletes nothing, since the post left no new message for the old one to give
  way to. A deletion that is discarded leaves both messages standing.
- **A job that fails stops its lane.** A lane is a list of jobs carried out in order, one at a
  time, and a job that fails, whether from Discord or from the bot (a post refused, a fault in the
  bot's own code, a check that raises as a change starts), stops that lane there until the job is
  cleared, save for the four exceptions to this rule below. Nothing behind it on the lane runs: a
  change that makes the stopped job's work no longer due cannot overtake it either. The other lanes
  carry on, a stop in one holding only that one. The change keeps its state and the stop is the
  job's own record (when it first failed, how often it has been tried, when it is next due), so it
  survives a restart. What the job's earlier steps saved stays saved, which is why anything that
  must be all or nothing is saved in one step; the failed step's own save is rolled back, so a try
  runs it afresh. Every job of a round's review is such a job, one a module cannot step over (the
  results and attendance specifications say which, and what each stop leaves). A new change asked
  for meanwhile is acknowledged as usual, joins the back of its lane and says that the lane is
  stopped; one that overtakes a stop says it runs past it, the stop standing (`held_behind_text`).

  **Four exceptions: a league's settings, `/bot init`, roles moved after a driver's or team's
  change, and the settings a league repairs itself with.** A change to one of a league's
  settings is one atomic save of its value and its audit record (its line with them, or in the
  save of the step after it that carries the line — the arming of the daily portrait job, a
  role's grant to every driver where it replaces one gone from the server while the season's
  configuration is fixed, or the old hub stood down and the new one's permissions set once its
  channel is moved; see "The record is the queue's"), and the work after it that the setting does
  not need (checking it against the templates, arming or removing a timed job, granting a role to
  every driver, asking the hub's panel to be posted again as the bot's own change) is work nothing
  later depends on. A fault in either leaves nothing half-done and nothing behind it on the lane
  waits on it. A fault in the save is rolled back whole, so nothing is saved and the member can
  simply ask again; a fault in a step after the save leaves the setting saved, so asking again
  with the same value saves nothing and repeats no step, and the reply says what was not done, or
  what to do by hand, where the step has anything to say. So such a step carries `Step.on_fault`:
  where it raises, the queue does not stop the lane but marks the job done with what `on_fault`
  returns, in a save of its own with its lines, and the change carries on to its next step or
  finishes. A change type may likewise give `ChangeType.on_check_fault` for a check that raises as
  a **member's** change starts: the change is ended as REFUSED in one save, with the failure line
  in place of a refusal's, and the reply it returns. What the member is told and what the log
  channel records of either is the core specification's "How a change is carried out"; the full
  detail goes to the host's log. A step that is `undiscardable` may not carry `on_fault`, since it
  is a step a stop must hold. `core/services/setting_change.py` builds both callbacks for every
  settings command once. *Rejected:* a step that catches its own fault, which would leave its
  partial writes on the save's connection and could not catch what fails after it returns, the
  queue's own audit and line writes and the commit; stopping the lane for a setting, which would
  hold every other change for a fault that either the member cures by asking again (a save rolled
  back) or that nothing later on the lane depends on (a step after it).

  `/bot init` (a change type of its own; see "The record is the queue's") ends on a fault rather
  than stopping the lane, both in its one SAVE step (`Step.on_fault`) and in a check that raises as
  a member's `/bot init` starts (`ChangeType.on_check_fault`): no league admin role is set while no
  server holds the league, before a first `/bot init` or after a pack, so nobody could clear a stop
  on it if it held the lane (`core/services/bot_init_change.py`). A fault in its save writes no
  line, its claim rolled back with it; a fault in its check writes the failure line where a server
  holds the league. While none does, there is no log channel, and the host's log holds either
  alone. *Rejected:* stopping the lane on a fault in `/bot init`, as any other job does, which
  would leave a stop nobody yet holds the role to clear.

  The same holds for a member's roles moved after their driver's or their team's change. A `/driver`
  or `/team` change saves the driver's or the team's rows and their audit record in one step, and
  the role changes Discord makes after it are work that save does not need and nothing later on the
  lane waits on. Each such step carries `Step.on_fault`, so a role Discord refuses is reported and
  the queue goes on; what the member and the log channel are told is the core specification's, which
  gains this exception with the work that builds it. The move of a held signup channel's
  permissions after a driver's account changes is likewise work the save does not need and nothing
  later on the lane waits on, but its step carries no `Step.on_fault`: it catches Discord's refusal
  of the permissions itself and ends with the problem in its result, so the queue goes on, and any
  other fault in it stops the lane as any job's does; what the member and the log channel are told
  is the core specification's ("A driver's accounts"). Every other job, and a bot's own change
  whose check raises, keeps the rule. *Rejected:* stopping the lane on a refused role, as other jobs do,
  which would hold every change behind one lost permission until Retry or Discard was pressed.

  **A fourth exception: the settings a league repairs itself with overtake a stopped head on the
  league lane.** They are of two kinds. The first is the four that let a league back into the bot:
  the log channel, the interaction channel, the interaction role and the league admin role, which
  the core specification's "How a change is carried out" and "Setting the bot up" name as a
  league's way back in when one of them is wrong or gone. The second is every setting that points
  the bot at a channel, the hub, the signup channel, a division's lineup, calendar, results,
  standings, verdicts, RSVP and attendance channels and the weather forecast channel among them: a
  stop notice for a channel gone tells the league to set a channel and press Retry, and a stop that
  cannot be discarded would hold the lane for good if that setting waited behind it. No stopped
  job, its own remedy's or another's, may hold either kind up. Each change type gives
  `overtakes_a_stop=True` (`Setting.repairs_the_bot`, set on each such setting and read once by
  `setting_change`, never a list of kinds kept by the queue). Where a lane's head is stopped and
  not yet due for a try, and a queued change behind it is of such a kind, `_heads` and `_choose`
  take that change instead: the lane still works one change at a time, and the choice is still
  deterministic, but a repair jumps the queue behind a stop rather than wait with everything else.
  The stopped head itself stays stopped, and every ordinary change still waits its turn behind it.
  Every other setting, the module switches and the base and driver roles among them, keeps the
  plain rule, one change at a time in the order asked.

  An overtaking change and its lane's stopped head are both RUNNING at once, which the schema's
  one-RUNNING-per-lane rule (`queued_changes_one_running`) would otherwise refuse: `queued_changes`
  marks every change of such a kind `overtook_a_stop` as it starts, and the index excludes it, so
  the rule still holds between every other pair of changes on a lane. *Rejected:* a lane of the
  repair settings' own, beside the league's — it would let two lanes write the league's
  configuration at once, each save re-judging the other's collisions, for no gain once overtaking a
  stop does the same job on the one lane. A channel setting's save re-checks nothing even though an
  overtaking change runs ahead of its stopped head's remaining jobs, because no change that can
  stop a lane gives a channel a job: a change that claims one of the channels a channel setting's
  check reads (`channel_registry_service.find_channel_use`'s sources) is a `Setting` or
  `/bot init`, and neither ever stops a lane, each ending on a fault instead (the docstrings of
  `setting_change.py` and `bot_init_change.py` say so); every command that sets such a channel
  keeps to this as it moves onto the queue. So no stopped head ever has a remaining job that claims
  a channel, an overtaking change runs to its own end before the head is tried again, and a channel
  setting's check as the change starts is still the save's last judgement of the state. That
  invariant is the one a later reader could tune away: a change that can stop its lane and claims a
  channel would need the save to re-check, or would need to be barred from being overtaken.

  Nothing else overtakes the stopped job, so whatever runs outside the queue leaves alone what a
  change is in hand on, stopped included, as the queue tells it (`unfinished` or `in_hand`; core
  specification, "How a change is carried out"). Discord answering that a message is already
  gone is no failure: a job that deletes it is done, and one that edits it is done too, with a
  line in the log channel saying the message was gone.

  A job is cleared in one of three ways, which the core specification's "How a change is carried
  out" holds, with the schedule, who may press each button and the lines written; what follows is
  the mechanism. *The bot's own tries:* it runs the job again, by running the owning module's post
  again, as text where it would have been a picture (Constitution XIV, rule 8), 1, 5, 10, 15, 30 and
  60 minutes after the job first failed, so that the last falls at the hour; a try that fails writes
  no line of its own but the last, which says the bot has stopped trying on its own. *Retry,* a
  button a league manager or admin presses at any time but while that job is being tried, and the
  only way on after the hour: the job is made due at once. *Discard,* a button a league admin
  presses, drops that one job, which the log records with what was not done, and the request's later
  jobs run on, each checking again whether it is still due. A change that had not yet begun, because
  its check failed, is dropped whole, unless its first job cannot be discarded. Where a discarded
  job leaves a review without what it stands on, the change's closing job asks, as a bot's request
  in a save of its own, for the review to open again (core specification, "How a change is carried
  out"; the results specification says which jobs).

  Which jobs cannot be discarded, and what the member and the log channel are told when Discard is
  pressed on one, the core specification's "How a change is carried out" holds; what follows is the
  mechanism. Such a step is marked `Step.undiscardable`, a reader rather than a flag: it gives what
  would follow from dropping the job, in the job's own terms, so that the refusal can name the round
  or the season. Discard on the job is refused as any refused press is, and saves nothing else: no
  discarded mark, no Discard line, no audit record, no follow-on, and the job stays the one its lane
  is stopped at. Retry, and the bot's own tries, clear it as they clear any job. A step may be
  marked undiscardable only where a Retry can always clear it: once its work is no longer owed, its
  `still_due` drops it or it completes doing nothing, so that a job no league can discard is never
  one that can only fail. The notice keeps both buttons, and it is the button that refuses: the
  notice is one persistent view with fixed identities, found again after a restart, and a notice
  without Discard would need a second view, chosen for each notice and registered at start.
  *Rejected:* that second view; and a discard that drops the job and names the loss, which leaves a
  league with nothing it can do to put it right. A change stopped at its check, before any job of it
  ran, sits on the change's first job, so its Discard is refused where that job cannot be discarded,
  and drops the whole change where it can.

  Both buttons are on the stop notice, the one log-channel message that says a lane is stopped
  and names the job, and the channel of a conversation's lane. Who a stop notice
  mentions is the core specification's rule; the mention is asked only for a notice that has
  not yet landed, so that a restart's line and an hour's line mention nobody. They work directly on the queue's own records, and are not changes put on the
  queue: the lane is stopped, so a change put on it could not run. Each press is saved with its own record (the line, and
  for a Discard the audit record), and a press that is refused is recorded as well. A press made
  while the worker of that job's lane is trying that job's change, its check included, is refused,
  to be made again once the try has ended: each press is taken under the lock the workers hold as
  they choose a change, and a press saved during a try could outlive the stop that try clears, so that a later
  failure of the job would be read as the press's.

  After a restart a stopped job stays stopped, as "When the bot stops" in the core specification
  has it: the bot makes no try of its own, whatever the schedule had left, and only Retry or
  Discard moves the lane (Retry alone where the job may not be discarded). A change a stop cut
  off with no job failed carries on from its first step not done.

  *Rejected:* a failed job stepping aside while later changes go ahead, and a job tried once and
  handed to the league, as turning results off once did for a message it removes. Each is a
  second rule beside "every failure stops its lane", which is the rule the core specification's
  "How a change is carried out" records. A conversation runs apart from a stop on the league lane,
  or on another conversation's, only because its work is its own, and what a conversation hands
  the league goes to the league lane's back. The first three exceptions (above) are not a job
  stepping aside either: the job is ended, not stopped, and nothing it leaves undone is waited on.
  The fourth exception is the one true overtake: the settings a league repairs itself with run
  ahead of their own lane's stopped head, by design, because a league locked out of the bot has no
  other lane to wait apart on, and a channel stop's remedy has to be able to run. It is kept
  narrow: `ChangeType.overtakes_a_stop` is read once from `Setting.repairs_the_bot` for those
  settings alone (`test_every_channel_setting_overtakes_a_stopped_job` holds the set to the
  channel settings and the two roles, and fails on a channel the registry gains without one), the
  stopped head stays stopped, and everything else on the lane still waits behind it.
  *Rejected:* a queue for each module, which leaves the conversations of one module waiting on each
  other and the league's work split across queues with no one order; and the league dropping a
  stopped conversation's job when it ends the conversation, which would let a change overtake a
  stop. Also rejected: an automatic try after a restart, which "When the bot stops" in the core
  specification rules out.
- **A failure that cannot be recorded is logged, and the job tried again.** Where the stop's own
  save raises, nothing is marked stopped: the lane's catch-all (`ChangeQueue._work_lane`, one for each lane) puts the
  error in the host's log, that lane's worker pauses and looks again, and the job runs again, so that its
  failure is recorded then. Where the save committed and only the notice's post raised, the stop
  stands and the notice is posted again (see "Posting to Discord").
- **The record is the queue's.** The queue writes the records the specifications ask of a change:
  for every change to the league's configuration, an audit record and a log-channel line (the core
  specification's "The record of what changed"), and for any other change, whatever log line its own
  specification asks for. No command writes them itself, but for `/bot pack` and the factory reset
  (above). A log line is put on the log line's own retry queue in the same save as the step it
  records (`OutputRouter.queue_log_on`), so a stop cannot lose it, and is delivered once the save
  has committed (`OutputRouter.deliver_queued`), under the lock the retry loop shares, so the queue
  and the retry loop never both send it. The queue is handed the router and writes through it, not
  through the bot, since a service does not use the bot to look up other services (above).

  A league's settings share the mechanism rather than each forming its own: a command declares a
  `Setting` (its check, its save on a connection, its reply and the tables it writes) and
  `setting_change` builds the change type, so that the line, whether an audit record is written,
  the answer for a value already held and the answer for a fault are decided in one place. A
  setting's value, its audit record and its line are one save, save where a step after it arms the
  daily portrait job, grants a role to every driver where it replaces one gone from the server
  while the season's configuration is fixed, or stands the old hub down and sets the new one's
  permissions once its channel is moved: there the value and the audit record share the save and
  the line follows in that step's own save, so that a fault of the bot's own in that step — a
  failed arming, a failed grant, the guild or the new hub's channel gone missing — records no
  success, while a permission edit Discord refuses, which the step reports rather than raises, or a
  base role gone from the server, records a success with that fault named beneath it. *Rejected:* a
  bespoke change type for each command, which forms the line, the audit and the fault answer a
  score of ways. `/bot init` alone is a change type of its own rather than a `Setting`: it claims
  the server, where a setting writes one column of a server already claimed
  (`core/services/bot_init_change.py`).

---

## The database

**All database code is in services**, or in core's database code for connections and migrations.
A cog never opens a connection, and neither does the entry point.

**Connections are lent again, not opened for every operation.** Opening one starts a thread and
makes SQLite read the whole schema before its first statement, and a change opens a great many.
`get_connection` therefore keeps a few connections idle and lends them to the next caller for the
same database, and each block still has one connection to itself. Core's database code does this
and nothing else does. Checking a connection out never waits: it takes an idle one or opens one,
because some code opens a second connection while its first is still open (a service reading
through another service inside its own block), so two callers at once always have two. A connection
is lent again only when it was given back clean and nothing on it differs from a new one; any other
is closed, which rolls back what it held. A connection that a save confined to its tables is closed
on return, never lent, and nothing outside the database code leaves anything on a connection (a SQL
function, an authoriser, a `row_factory`, a PRAGMA, an ATTACH or a temporary table), because the
next caller would inherit it. The migrations and a restore keep out of this: the migrations use a
connection of their own, so nothing holds the database open when they finish and a factory reset
can delete the fresh copy it migrated, and a restore runs before any connection has been lent. The
entry point closes the idle connections as the bot stops, as wiring, since each holds a thread that
would keep the process alive. The cap, the closing of cursors on return and the reliance on the
connection library's internals are in `get_connection`'s docstring, each pinned by a test.

*Rejected:* one connection held for the bot's life, since a service reading through another
service inside its own block needs a second connection while its first is open, and one connection
cannot be two. *Rejected:* a capped pool whose checkout waits for a free connection, since that
second read would wait on the block that asked for it, and neither would finish.

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

**Each table is read and written by its owner alone.** Every statement that reads or changes a table
lives in the module that owns it, core included, and every other module asks the owner: a module
asks core through core's services, which it may import, and core asks a module through a hook, or
the image module through its protocol (above), since core imports no module. A query that would join
another owner's table is split at that line, each half asked of its owner on the connection the
asker was handed, where it has one, so that both halves read inside the same save. Each module's
design file lists its tables, and the checks of this rule hold the owner of every table (see "How
the rules are checked"), failing on a table added without one, on a statement that changes a table
outside its owner, and on one that reads it there. Results records a round's attendance through
`AttendanceAfterReview` (above), so the attendance tables are written by attendance's code, on the
connection results' step was handed. Where a module keeps its own columns on a core table (weather
on rounds, sessions and a division's forecast channel, attendance on rounds, and the weather and
signup on/off flags on the settings row), that module's design file names those columns as an
exception. Those columns are that module's alone, to read and to set, and core does not set them
either; the rest of the row is core's. The other modules keep their on/off flag in their own
settings table. A round's status is core's, though results moves it through the stages of a round's
results: results asks core's service to set it, so a round can still be cancelled while results is
switched off.

*Rejected:* letting any module read any table, with the owner's design file naming the columns
others read, which costs nothing today but leaves a change to a table reaching readers in other
modules that only the tests would find. *Rejected:* closing only the modules' tables and leaving
core's open to every module, on the ground that modules depend on core, which leaves a change to a
core table reaching readers in every module. *Rejected:* moving each module's columns off core's
tables now, which touches every reader of those columns before go-live for a cleaner line of
ownership.

**A conversation's lane writes only its own conversation's tables, and that is held at run time.**
A change type of a conversation's lane declares the tables its kind writes (`ChangeType.writes`):
its conversation's own rows, never the league's. A change type that declares `writes`, on whichever
lane it runs, has the connection of every save made for it, a saving step's or a record's, given an
SQLite authoriser (`_confine` in `core/services/change_queue.py`) that refuses an insert, update or
delete on any table but those. The queue's own tables are always allowed (`QUEUE_TABLES`: the job's
mark, its audit records, its log lines and the changes it asks for), so the mark, the lines and a
follow-on pass, and an empty `writes` allows those alone. A write the authoriser refuses raises in
the step, rolls its save back whole and stops the lane at that job, so it never lands; in a
league's setting, `/bot init`'s save or a role move, whose steps end on a fault (see "A job that
fails stops its lane"), it ends the job instead and the lane goes on. A change type whose `writes`
is `None`, the default, is not restricted; most league change types leave it so, and a league
change type may declare the tables it writes and be confined to them likewise, as replacing a
round's results with its resubmission drafts does. What a conversation hands the league, it asks
of the league lane (see "How a change is carried out").

*Rejected:* trusting each step to keep to its tables, which a lane's own tests would never catch
once the flows are many; and a separate connection for each lane, which SQLite would not make a
second writer.

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

**Deletes are proven by a test, and each module deletes its own rows.** Deleting a season, division,
round or driver removes what hangs off it, and a list of other modules' tables kept by hand in core,
not updated when a module added a table, has made a delete fail or leave rows behind. So core
deletes only its own rows. For each kind of delete (a season, division, round or driver deleted, and
a driver merged into another) it declares a hook, and each module signed up deletes or re-points its
own rows on the connection core's delete is handed, in the same save, before core deletes its own,
so no link is left pointing at a row that is gone. A link from a module's table to a core table
takes no action on its own, so nothing a module keeps is removed by the database on core's behalf. A
test fills a database with one row in every table, fails when a new table is not filled, and runs
every kind of delete against it. Within one owner's tables, a link may be changed to delete
automatically, one at a time, only where the linked rows plainly belong to what is being deleted.
Never where the link is there to stop a delete, as a placed driver stops their division being
deleted (the core specification's "Divisions"). A module's design file names any link of its own
that is there for that reason.

*Rejected:* letting the schema delete a module's rows from core's, by a cascade on the module's
link, which removes them without the module's code taking part. *Rejected:* letting the database
delete linked data almost everywhere, which can quietly remove history. *Rejected:* only writing
down how each link should behave, which would never run a delete.

**A database update is applied whole, or not at all.** Each migration runs in one transaction with
foreign keys switched off, and the keys are checked before it commits. This is SQLite's own
procedure for changing a table ("Making Other Kinds Of Table Schema Changes", in its ALTER TABLE
documentation). With the keys switched on, rebuilding a table would silently delete the rows linked
to it by a cascade, empty the links set to be emptied, and fail on the rest. The rules for the
schema baseline itself are CLAUDE.md's.

**What going live changes for the schema is listed in one place,** the `run_migrations` docstring,
so nothing is missed on the day. A check about the schema reads the schema the migrations build,
never one migration file's text, so that the day itself changes nothing about it.

**A column nothing reads is removed, not kept.** A stored value nobody reads still has to be
written, and goes stale without anyone noticing.

---

## Timed work and restarts

The bot is mostly driven by the clock: weather phases, check-in calls and deadlines, results
channels opening, clean-ups. It also has to carry on correctly after it has been stopped. The whole
bot does this in the shape `steward_module.md` §3 designs for stewarding: the database holds the
moment work is due, a job only wakes the bot, the scheduler drops no job for being late, and one
sweep at start-up works through what was missed, in order, before the scheduler runs. §3's heartbeat
and downtime extension stay stewarding's own.

*Rejected:* using this shape for stewarding only, which leaves the bot with three different ways
of recovering: the entry point's own steps, the results module's "results posted" flag, and
stewarding's own design. *Rejected:* reporting half-done work for a league manager to repair by
hand instead of finishing it.

**The database says when something is due.** A timed event is a row with the moment it is due. The
scheduled job (APScheduler) only wakes the bot at that moment, and calls its kind's handler, the
same one the start-up sweep calls for a missed event (below), so a job run on time and one caught up
take one path. The handler reads the row again and acts only if the row still says the work is due.
A job that fires after its work was changed or cancelled then does nothing. The job store becomes a
convenience: if it were lost, the bot could rebuild every job from the database.

**Each kind of job's handler decides what happens if it is missed.** When the bot was down at the
moment a job was due, the handler its module provides for that kind of job (below) decides what
becomes of it: whether it runs late or is skipped, or whatever else its module's specification asks.
The handler records a skip on its own module's record of the event (its own table, or its own
columns on a core table), as a change on the queue like any other, so the late job, or the change it
asks for, finds nothing due. Every job is to be armed with no limit on how late it may run
(`misfire_grace_time=None`), so that the scheduler never drops one on its own and its default
grace never decides; the jobs armed otherwise are those the lateness check in
`tests/repository/test_architecture_rules.py` lists. What each kind does when missed is a rule a
league notices, so it belongs to the specifications (the core specification's "When the bot
stops", and the stewarding specification's [STW-RST-001] to [STW-RST-003]).

*Rejected:* declaring for each kind of job whether a missed one runs late or is skipped, for the
sweep to apply, which puts a module's rule in core.

**Jobs are made only through the scheduler service.** Nothing else arms, finds or removes a job. A
round's job is named from the round and the event, so arming it again replaces the old one instead
of adding a second (the `_round_job_suffix` docstring in the scheduler service). A round's timed
work is armed by the scheduler service's own functions. Each reads when the round's events fall from
`round_event_moments`, but for the clean-ups. Every clean-up is armed at the moment
`clean_up_moment` gives, and `round_event_moments` places both clean-ups by it too. `schedule_round`
arms a round's forecasts and, through `schedule_result_submission_jobs`, its results submission;
that function arms only the submission, and a season's approval and an amendment also call it on its
own. `schedule_all_rounds` arms a season's rounds through `schedule_round`, and
`schedule_attendance_round` a round's check-in. An arming function that skips a job whose moment has
passed judges it against the `now` it is handed ("The time is always passed in"). Beside them,
`run_result_submission_now` runs at once the submission of a round whose moment has passed. Only
three changes call them: a season's approval, the amendment of a round (`round_amend_change`), and
the re-arming of a round whose timed work a refused cancellation took, a change of its own
(`round_rearm_change`, `season.round.rearm`) built from the amendment's steps. The arming step those
two share calls `run_result_submission_now` for a round still to be run whose moment has passed,
save under test mode. Every path arms through these functions. A restart arms nothing, the job
store keeping the jobs and the start-up sweep catching up those missed (above), and neither does
test mode, whose advance brings forward work already armed or found due in the database. A round's
next-day clean-ups, which its weather and its attendance arming both owe, are armed with them: the
forecast's by `schedule_round` and the check-in's by `schedule_attendance_round`.
`schedule_round_cleanups` arms either or both on their own, and the re-arming calls it alone for a
round raced, or ended cancelled because every session was entered as not held.

**A delay is a one-off timer that asks a change.** The queue holds no delay of its own. Where
something must happen a set time after a job (a season review's, a correction's or an
amendment's five minutes running out, a signup left unanswered for a day, a held signup channel's
24 hours, the review channel's lock warning taken down after eight seconds), a job of its own,
after the one whose record saved the row the timer is keyed to, arms a one-off timer through the
scheduler service (`SchedulerService.arm_once`, named from its kind and its row, so that arming it
again replaces it). It is armed with no lateness limit, so one missed while the bot was down fires
when it starts. The builder registers each kind's callback before the scheduler starts
(`register_timer`). When the timer fires, its callback reads the row again and asks its
change of the queue, as the bot's, or asks nothing where the work is no longer due; a callback that
raises goes to the failure path of scheduled jobs, the job runner (see "Errors and failures").

Which lane a timer's change runs on is the core specification's ("How a change is carried out",
where a conversation's own timers run on its lane); the callback asks its change as any request is
asked, and the change type gives the lane from the payload (`ChangeType.lane`).

**Start-up runs once, in one function.** discord.py can report the bot as "ready" more than once in
a single run, after a network drop, so start-up work does not belong in the handler for that event.
Steps 1 to 4 run in the entry point, before the bot connects; steps 5 to 7 are one function,
`start_up`, which the handler awaits and which runs once per process. Start-up:

1. reads the settings (the token, the database's path and the job store's, an unset job store
   resolved beside the database) once, in the entry point, from the environment and the `.env` file,
   rather than on import. A module's own host setting (`INKSCAPE`, for the rasteriser) stays the
   module's, read when it is used and never on import; the entry point's `.env` load is what
   supplies it;
2. swaps in a saved state waiting to be restored, before anything opens the database or the job
   store (the core specification's "Saving a state and returning to it");
3. applies the migrations, before connecting to Discord;
4. runs the builder: the services, the cogs, the hooks and the kinds of timed job;
5. runs the start-up sweep once, when Discord first connects, while every other change, whether a
   member or a Discord event asks for it, waits until the sweep has put its own on the queue, so
   none overtakes a missed event that would have happened first;
6. starts the queue, which carries on with any change a stop cut off, leaves a job that was stopped
   on a failure stopped, to be cleared by Retry, or by Discard where the job may be discarded, and
   posts again any stop notice that never landed;
7. only then lets scheduled jobs run.

The retry loop for undelivered log lines is not one of these steps: it starts from
`LeagueBot.setup_hook`, once per run at login (under "Errors and failures", the item on Discord
events). Its `before_loop` waits for the bot to be ready, so it may run beside the sweep, which is
safe because it delivers only log lines already owed, with its own lines about them, and asks for
no change.

Tests can then run the whole start-up in order, instead of checking its order by searching the
source code.

**One sweep picks up the timed events that were missed.** It walks everything that came due while
the bot was down, in the order it would have happened, and hands each to its handler. A timed event
that makes a change puts it on the queue, like any other. A module's start-up work that is no missed
event (a review posted again, an interrupted submission reopened, the hub's panel posted again, as
the core specification's "When the bot stops" lists) is a step of the sweep too, reached through the
hook for the bot starting. Each step of the sweep is kept separate, so one failing is reported and
does not stop the rest. A step holds no module's rule: the entry point's step for putting a round's
review back calls the results service that decides it (core specification, "When the bot stops"),
and that decision is asked of the queue as a change like any other.

**The sweep only delegates.** It is core's: it asks each kind of job, through what its module signed
up, which of its events came due, puts them all in the order they fell due, and hands each to the
handler its module provides for that kind of job, one at a time: each handler finishes before the
next is called, and none runs beside another. Before any of them, it calls whoever signed up for the
bot starting, one at a time as well, so a module's start step's change is queued, and carried out,
ahead of any change its missed events ask for. A handler asks for its change on the queue; what an
earlier event changed is seen by a later one's change when the queue runs it, not by the later one's
handler. The sweep holds no module's logic. The builder signs each handler up with its kind of job,
together with the kind's own way of telling which of its events came due, as it registers the kind
with the scheduler service; that is the hook core offers for a timed event falling due. The handler
holds its module's logic, what becomes of a missed event included; core holds none of it. Each
module writes the handler for every kind of timed job it has, and the entry point holds none of it.

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
every time inside the bot is in UTC. The change queue's clock (`ChangeQueue.now`) is the bot's
clock: whatever hands a service its `now`, a cog, a change type or the start-up sweep, reads it
there, so that a test moving it moves every reading. A stored time is written from the `now` handed
in, never by SQLite's `datetime('now')`, and in one form, ISO 8601 with its UTC offset, a round's
moment included, so that no reader handles a naive time and no equality test of a naive time with an
aware one, which Python answers as unequal without an error, can arise. *Rejected:* keeping a
round's moment without an offset and making it aware where it is read, which leaves every reader to
remember the conversion. How tests treat `now` is CLAUDE.md's.

---

## Posting to Discord

**One small handler for each kind of post.** The posts the bot makes fall into four kinds, and each
kind has its own rules for failures and for later changes:

| Kind | Examples | What sets it apart |
|---|---|---|
| **Log line** | configuration changes, outcomes, failures | sent to the log channel and retried by its own handler, not by the change queue; what a log line may hold is the core specification's "The record of what changed". The queue's stop notice is a log line with buttons, which the router posts without putting it on that retry |
| **Standing post** | calendar, lineup, standings, results table, attendance sheet, forecast | each new one replaces the last, and the old message is deleted after the new one is up |
| **Notice** | a cancellation notice, a verdict, a round's check-in call (edited in place as drivers answer) | its id is kept wherever it might later be edited or deleted |
| **Bot-owned channel** | a results submission channel, a driver's own signup channel | deleted whole with its channel; only a post the bot must find again, such as a prompt it replaces, has its id kept |

The code that makes a post still builds its own text, pictures and buttons, and saves the message id
it is handed back. The notice, standing-post and bot-owned channel handlers do the sending and
nothing else: the caller states who the post may mention (a required argument, so that no post
leaves it to Discord's default), and the handler hands back the message or its id and, when asked,
edits or deletes a message or deletes a bot-owned channel whole. They **raise what Discord raises**,
a `NotFound` included, so that the queue's own handling of a message already gone, and a caller's
reason of its own wrapped in `StepFailedOnDiscord`, work as they would for a call made directly. A
log line differs: the writer switches its mentions off itself, splits a long one, and contains its
own failures rather than raising them. Every log line but two is kept until it is delivered,
whatever keeps the log channel from taking it (the core specification's "The record of what
changed"): a line posted on its own (`post_log`) is put on the retry queue where it cannot be
posted, its channel gone, refused or no text channel included, and a line a change saves is on that
queue before it is sent (see "How a change is carried out"). A waiting line is delivered to the log
channel as it is when it is tried, so that a channel set again after the old was deleted takes the
lines that waited for it. Two kinds of line are never queued, each going to the host's log where it
cannot be posted: the factory reset's closing line (below), and the retry worker's own lines, so
that a row stuck while the channel is unreachable cannot queue a warning about itself on every try.
The stop notice is posted again its own way (below). `send_log_text`, the retry's own send, raises
what Discord raises to the retry worker, which counts it. Retrying is the change queue's, except for
log lines. Each handler is a service in core, in a file of its own:

| Handler | File | Its functions |
|---|---|---|
| Log writer | `core/services/output_router.py` | the router, and `send_log_text`, which divides a line and sends it mentioning nobody, for the retry of a late one |
| Notice handler | `core/services/notice_handler.py` | `send_notice`, `edit_notice` and `delete_notice` |
| Standing-post handler | `core/services/standing_post_handler.py` | `post_standing`, `edit_standing` and `delete_standing` |
| Bot-owned channel handler | `core/services/owned_channel_handler.py` | `post_in_owned_channel` and `delete_owned_channel` |

Every function of the notice, standing-post and bot-owned channel handlers carries
`posts_in_a_job`: they run in the jobs of a change, and a post off the queue cannot go through one.
The posting check lists what each module still posts itself, each under the module pass that moves
it.

*Rejected:* one gateway for every post, which would need to know every module's rules.
*Rejected:* one handler per channel, which repeats the same rules a dozen times.

**Every post is made in a job.** Every message the bot posts, edits or deletes, and every channel
it makes or deletes, is a job of a change on the queue, so that each is recorded, is tried again on
the queue's schedule, and stops its lane where it fails (see "How a change is carried out"). The
rule has two halves:

- **The decorator.** A function that posts carries `posts_in_a_job` (`core/utils/in_a_job.py`),
  which raises before the post is made where it is called outside a job. The worker holds the
  running job's number in a context variable around a job's `run` and `record` and nothing else,
  so a change's check and a job's `still_due`, which run outside the job, see none, and code that
  is not a step cannot claim to be one. The raise is a fault in the bot's own code: it stops the
  lane at the step that made it, as any such fault does, and is never taken for a refusal by
  Discord to be tried again as one. *Rejected:* a flag on the bot or on the queue, which one
  lane's job would set for every other lane's code to see; and the job's number passed down to
  each posting function, which a caller outside a job could invent.
- **The check.** A test fails on any function that posts without the decorator, unless it is named
  off the queue with its reason (see "How the rules are checked"). The router
  (`core/services/output_router.py`) alone is exempt from it whole (`JOB_CHECK_ALLOWED`): it writes
  log lines, the stop notice and the turn-away, and nothing else, and what it posts off the queue
  are the exceptions below. The other handlers are held to it. The check of posting outside the
  handlers passes over the four handler files (`POSTING_ALLOWED`) and over a function named off the
  queue, which runs outside a job and so cannot go through a handler. Two functions whose sends look
  like posts and are not are named in `NOT_A_POST` with their reasons. One is the owner's own
  `!sync` command (`guild_sync` in the entry point): it deletes the owner's message and posts a note
  that deletes itself, which is a prefix command's reply. The other is the queue's edit of a
  member's own acknowledgement (`ChangeQueue._update_reply`), which answers the member and posts in
  no channel.

The exceptions, each with its reason:

- **A log line,** delivered by the router and sent again by its own retry (below), and **the stop
  notice,** which says that a lane is stopped and so cannot wait on one, with its buttons taken off
  once the job clears (`OutputRouter.strip_view`), which cannot wait on the lane either.
- **A reply** to a member's command or press, and the queue's update of its acknowledgement with
  the outcome included: it answers the member and posts in no channel. A press's own edit of the
  message pressed is a reply too, though the message is one a job posted where a channel reads it:
  the results picker's choice (`_ConfigSelectView`) replaces the text of a message in the
  submission or amendment channel and takes its buttons down. It is the interaction's own response
  to the press, Discord's one answer to it, and the buttons come down with it. A second press made
  before they are down is refused by the view itself (results specification, "Submitting round
  results"): the first press claims the choice before its edit, so the message names that press's
  choice, and the press puts the message back, buttons and all, where the queue then refuses the
  choice or fails on it. The `_ConfigSelectView` docstring says when the claim is released.
- **The factory reset's clean-up and its progress.** The reset empties the database the queue
  lives in and leaves no log channel or roles for Retry and Discard, and its progress is the reply
  to the owner's own command.
- **The turn-away.** A message typed into a conversation's channel while an earlier turn there
  waits is answered at once with a note mentioning its author alone, and deleted
  (`OutputRouter.turn_away`). Each is tried once; a refusal of either is logged on the host and
  lost, never tried again, and the note stays. Queued, the note would come after the very turn it
  explains, and could stop a lane for a courtesy. Beside it, the ⏳ mark a typed message is given
  at once while its turn waits is a reaction made off the queue for the same reason, a refusal
  logged and the turn going ahead (`core/services/typed_turns.py`); taking it off is a job, the
  turn's last, so a mark that cannot be taken off stops the lane and is tried again.

**Every post keeps three things,** whichever handler sends it:

- **Its message id,** wherever the bot might later edit, delete or replace it. The owning module
  saves the id the handler hands back in the posting step, and asks the handler to delete the
  message it replaces in the step after, so each table keeps one writer. A message nobody recorded
  can never be found again, to be edited or deleted.
- **Who it may mention,** stated where it is sent. A log line's mentions are wrapped so that they
  name without notifying, but one: the stop notice's mention of the league admins' role
  (`OutputRouter.post_notice`), the one mention the log channel notifies. Who it mentions is the
  core specification's rule, and when the queue asks it is said under "How a change is carried
  out".
- **What happens if it fails:** a post made in a job stops its lane, is tried again on the
  queue's schedule, and is named in the log channel; a post off the queue fails as its exception
  above says (a log line that keeps failing, in the host's log). A log line the log channel cannot take is also
  told to the member it records (the core specification's "The record of what changed" holds who
  is told and when): the router finds the interaction through `core/utils/answering.py`, which
  records the one a task is answering, or takes the one the change queue holds, and the host's log
  alone records the failure where none can still be answered.

  The factory reset's closing line is not queued (the core specification's "Factory reset" section
  holds the rule for what it says and when). It is posted after the reset has wiped the database, so
  it is neither queued nor told to anyone, for two reasons. A queued row would sit in the fresh
  database of a bot serving no server (`OutputRouter.post_log`'s docstring). And the member's notice
  says the line is kept and delivered later (the core specification's "The record of what changed"),
  which this one never is, so the core specification's "Factory reset" has a line that cannot be
  posted written to the host's log. The cog learns the log channel from the router before the wipe,
  and hands it to the router's `post_log`, which posts the line there and queues nothing.

**A failed post is sent again by its owning module, on the queue's schedule.** A post that fails
is a job that fails, and stops its lane (see "How a change is carried out"): the
queue runs the owning module's post again, as text, on that schedule. The text is then written at
the moment it is finally sent, and the owning module saves the new message's id as it always does.
No queue ever holds a picture; the constitution's Principle XIV, rule 8, says why. The old retry
queue is kept for log lines only.

The stop notice is the one log line that must keep its buttons, so it is not put on that queue,
which would send it again without them. It is posted through the router after the stop is saved,
the message it lands as is kept on the job, and the worker posts it again while none has landed, at
the retry marks and hourly after them. The member is told where it fails, as for any log line the
log channel cannot take.

*Rejected:* the retry queue re-sending its stored text and writing the new message's id back
itself. *Rejected:* no longer retrying these posts. *Rejected:* keeping the retry queue for posts
beside the change queue, as two mechanisms for one job.

Which channels exist, and what may go in each, is the constitution's Principle VII and the channel
registry (`channel_registry_service`).

---

## Errors and failures

**Each kind of starting point has one failure path.** Whatever fails in the end, the full error goes
to the host's log and, but for an autocomplete (below), one line goes to the log channel, and
reporting it never raises another error. A job the bot is trying again is reported as it first
fails, again after the last of the bot's own tries, and at each Retry that fails, and not at the
bot's other tries. A change that succeeds is recorded in the log channel as its specification asks;
a failure always is. A command that asks for a change meets two in turn: `report_failure` if the
request itself fails, and the queue once the change is under way.

- **Changes**, from whatever starting point, are reported by the queue: the outcome to whoever asked
  while their reply can still be updated, and otherwise to the log channel (see "How a change is
  carried out"), and to the log channel as the change's specification asks.
- **Commands, buttons and forms** go through `report_failure` (`core/utils/interaction_errors.py`),
  called by the `LeagueCommandTree`, `LeagueView` and `LeagueModal` base classes. What the member
  and the log channel are told is the core specification's "When a command fails". A results
  amendment runs as changes on the queue, and a fault in one stops its lane and is reported as any
  change's is, by the queue, but for the `names` job of its lapse and its
  cancel, which goes ahead without the names (a stated exception, below).
- **Scheduled jobs and repeating loops** go through one job runner.
- **Discord events** (a message, a member leaving) go through one error handler, a function in
  `core/utils/league_server.py` beside the base classes, which the builder attaches to the bot.
  `LeagueBot` declares the bot's attributes, and its one behaviour is `setup_hook`, which, once per
  run and at login, starts what the builder hands it (the retry loop for undelivered log lines). The
  error handler is never a method of the bot.
- **Autocompletes** go through `bounded_autocomplete` (`core/utils/autocomplete.py`), which answers
  with no choices and keeps the error for the host's log. It writes nothing to the log channel: an
  autocomplete changes nothing, and Discord gives it no message to answer with.
- **Background tasks** go through the helper that starts them.
- **The start-up sweep** goes through the job runner, one step at a time, like a scheduled job.

**A refusal is not a failure, and has helpers of its own.** A command, button or form that
refuses answers the member itself through `refuse` (`core/utils/log_lines.py`), which replies,
seen by the member alone, and writes the refusal's one line to the log channel; a confirmation
cancelled or left to lapse is recorded through `record_abandoned`, beside it. Each standard line
is formed there and nowhere else, so that every refusal, cancel and lapse names its member alike,
as the core specification's "The record of what changed" asks. `log_lines` lives apart from
`interaction_errors` because `record_abandoned`, which a scheduled lapse calls with no interaction
to name its member from, reaches the league's server through `league_guild`, and
`core/utils/league_server.py`, which defines that, imports `interaction_errors`. `refuse` sits
beside it so that each standard line is formed in one place. Like
`report_failure`, neither raises, and `refuse` attempts its reply and its line each on its own.

**Every line for the log channel goes through the log writer** (`OutputRouter.post_log`, or
`queue_log_on` and `deliver_queued` for a line a change's step saves, `post_notice` for the stop
notice, and `send_log_text` for the retry of a late one), which divides a record too long for one
message on its line breaks where it can, and at the limit where one line alone is too long; a reply
too long for one message is sent in parts through `chunk_message` (`core/utils/messages.py`), which
does the dividing for each of them. That is how a record meets the core specification's "The record
of what changed" on length.

**No cog handles its own errors, and there is no shared base class for cogs.** Every failure a
command does not catch itself reaches `report_failure` through the command tree. The tree steps
aside for any command or cog that has its own error handler (`LeagueCommandTree.on_error` in
`core/utils/league_server.py`), so one added handler would quietly switch `report_failure` off for
everything it covers.

*Rejected:* a `LeagueCog` base class. It would hold two lines of code, and an error handler added
to it would switch off the one failure path for every command.

**A catch-all error handler** (`except Exception`) is allowed in only five places, and in the
stated exceptions below them:

1. on one of the failure paths above. That includes a command, button or form that catches
   everything around its own work only to hand the error to `report_failure` itself, adding
   context to `what` (the round a `/round amend` confirmation was amending) or stating
   `outcome` (what became of a change it undid, in place of "may have been partly done"), and
   then returns. One failure still makes one line, and the base classes are spared a second
   report. The change queue's worker is a failure path too, and the
   fallback where a stop cannot be recorded: the lane's catch-all (`ChangeQueue._work_lane`, one
   for each lane) puts the error in the host's log, that lane's worker pauses and looks again, and
   the job runs again, so that its failure is recorded then; beside it, the dispatcher's own
   catch-all (`ChangeQueue._work`) puts a fault in starting the lanes in the host's log and pauses
   before it looks again. The queue's catches of a step that raises (`ChangeQueue._run_step`), of a
   check that raises as the change starts (`ChangeQueue._start`), and of a hook that raises in
   either (`ChangeQueue._step_failed`, `_end_on_check_fault` and `_refuse`, each leading to the
   lane's stop) are failure paths as well: the catches of a step and of a check each stop the lane,
   or, for a step of a league's setting, `/bot init`'s save or a role move that gives
   `Step.on_fault`, end the job instead, or, for a member's change of a type that gives
   `ChangeType.on_check_fault`, end the change, and the lane goes on (see "A job that fails stops
   its lane"), and a hook's catch always leads to the stop. Either way the fault is logged on the
   host and recorded in the log channel as a failure, once — save a fault in `/bot init`'s save,
   which writes no line, and a fault in its check while no server holds the league, when there is no
   log channel to record it in; the host's log holds either alone;
2. where the bot works through a list (divisions, drivers, posts) and one item failing must not stop
   the rest. Inside a queued change, each item is a job: one that fails stops its lane until
   it is cleared, and a league admin who discards it lets the items after it go ahead. Outside one, the failure goes to the failure path of whatever started the
   work (above). Neither applies where an operation's own rule is all or nothing, as a points
   amendment's save is (the results specification's "Changing points system mid-season");
3. around reporting a failure or a refusal, or writing a line to the log channel, where that itself
   might fail:
   keeping a line for retry (`OutputRouter._enqueue_if_configured`), recording a refusal already
   answered (`log_lines.record_refusal`) and naming a member in a line (`log_lines.name_of_member`)
   are this place's too, a line that can be neither posted nor kept being logged on the host and
   dropped, a refusal's record that fails not being answered a second time, and a member who cannot
   be looked up being named by mention alone, because a line to the log channel must not fail the
   work it records (their docstrings say so);
4. around a clean-up that then raises the error again;
5. around work a specification gives a fallback for, as a picture that cannot be drawn is posted or
   shown as text (Constitution XIV): the handler returns the fault as a line of its job, or of the
   posting it hands back, so that the fallback is recorded where the job's lines go, as the season
   review's `draw_and_post` (`core/services/season_review_changes.py`) does.

Beyond the five places, a stated exception has a catch-all of its own, each with its reason. The
callback a change type may give for a Discard of a change before it started
(`ChangeType.on_discarded`) is not a failure path but a stated exception: the catch-all around it
(`ChangeQueue._discard_follow_ons`) puts the error in the host's log alone, with no line to the log
channel, and asks no follow-on, because a Discard is the queue's last way past a stop and a callback
that raises must not hold it; the reader that says what dropping an undiscardable job would leave
(`ChangeQueue._undiscardable_refusal`) is a stated exception likewise: its catch-all puts the error
in the host's log with the full details, and the Discard is refused all the same, naming the job in
place of the reason, its refusal recorded in the log channel as any refused press is, because an
undiscardable job is never dropped for want of its reason; the `names` job an amendment's lapse and
its cancel share (`amend_end_changes._names_step`) is a stated exception as well: its catch-all puts
the error in the host's log and completes the job with no names, so the revert goes ahead with a
full tie ordered by user id, because a tie so ordered is the lesser fault than a round not put back
(its docstring says so); the helper through which a placement's capacity guards count the seats and
ask the image module (`PlacementService._ask_drawing`) is a stated exception too: its catch-all (a
switch nobody signed up for aside) puts the error in the host's log in full, and the guard lets the
placement through as if no problem were found, because a capacity check that cannot run must not
block a placement (its docstring says so); the image module's calendar gate
(`ImageDrawing.calendar_wanted`) is a stated exception likewise: its catch-all (a switch nobody
signed up for aside) puts the error in the host's log alone, with no line to the log channel, and
answers that the league does not draw its calendar, so the text calendar goes out, because a gate
that cannot be read must not lose a league its calendar (its docstring says so); the image module's
lineup gate (`ImageDrawing.lineup_wanted`) is a stated exception beside it: its catch-all (a switch
nobody signed up for aside) puts the error in the host's log alone, with no line to the log channel,
and answers that the league does not draw its lineup, so the text lineup goes out, because a gate
that cannot be read must not lose a league its lineup (its docstring says so); the helper through
which the season's own capacity checks count and ask the image module
(`approval_checks.ask_drawing`) — the review's and the approval's (`lineup_problems`, and the
review's calendar and attendance warnings) — is a stated exception beside
`PlacementService._ask_drawing`'s: its catch-all (a switch nobody signed up for aside) puts the
error in the host's log in full and lets the season through as if no problem were found, because a
capacity check that cannot run must not block a season (its docstring says so); the round guard that
`/round add` and the round imports share (`calendar_round_refusal`, in
`core/services/round_add_changes.py`) is a stated exception beside it: the change types' checks call
it when the change is asked for and again as it starts, and its catch-all (a switch nobody signed up
for aside) puts the error in the host's log in full and lets the round through as if no problem were
found, because a capacity check that cannot run must not block a round (its docstring says so);
the season's reading of the image module's configuration faults
(`approval_checks.image_configuration_faults`) is a stated exception too: each of its catch-alls
puts the error in the host's log; a fault reading the templates is stated as a fault of its own,
which refuses the season, because a check that never ran is not a pass, while a fault reading the
per-tier colours or the portrait settings (a switch nobody signed up for aside) stands aside and
lets the season through, because a reader that cannot answer must not be the thing that refuses a
season (its docstring says so); the season's
team-name check (`approval_checks.team_name_problems`) is a stated exception too: its catch-all puts
the error in the host's log alone and finds nothing wrong, so each review and confirmation that
checks the names carries on with them unchecked, because the check only keeps a team name from
becoming a bad artwork filename (its docstring says so); the season cog's own image review section
(`SeasonCog._build_image_review_section`) is a stated exception beside
`approval_checks.image_configuration_faults`: its catch-all (a switch nobody signed up for aside)
puts the error in the host's log and states the section as unreadable in the review, because a
review must never fail for this (its docstring says so); the image module's artwork signature
(`ImageDrawing.artwork_signature`) is a
stated exception too: its catch-all signs a configured directory it cannot resolve as
`[column, value, "unresolvable"]`, with a warning to the host's log alone, and signs the rest all
the same, because a fault that escaped it would empty the whole fingerprint and the review would
read as changed in every area (its docstring says so); the queue's own telling, of a change's
outcome, of its stop or of a press on a stop notice (`ChangeQueue._acknowledge`,
`_acknowledge_saved`, `_name_job`, `_post_notice`, `_tell`, `_tell_origin`, `_update_reply` and
`_finish`, and `OutputRouter.strip_view` beside them), is a stated exception as well: each catch-all
puts the error in the host's log and what the queue was doing stands, the change, its stop or the
press alike, a stop notice not posted being posted again, because telling what happened must never
undo it (their docstrings say so); the readers that stand aside (`amendment_service.approval_faults`
and `approval_checks.channel_on_server`) are stated exceptions too: each puts the error in the
host's log and reads an attendance switch as off (a switch nobody signed up for aside) or a channel
as present, because a reader that cannot answer must not refuse an amendment or a season (their
docstrings say so); a season's fingerprint that cannot be taken
(`season_fingerprint_service.take_fingerprint`, a module's fault in handing over its areas included,
a switch nobody signed up for aside) is a stated exception: it is signed as an empty one, which
differs from every fingerprint, another empty one included, and so refuses an approval whose review
was read, because a fingerprint that could not be read must not wave one through (its docstring says
so); the factory reset's Discord clean-up (`bot_cog._clean_up`) is a stated exception: it says in
the reset's last report and closing line that its clean-up stopped, because a reset must not end in
silence (its docstring says so); and the scheduler's removal of one job
(`SchedulerService._remove_job`) is a stated exception: every cancel is a clean-up, so a job that
will not go is logged on the host and left, a teardown list going on past it (`SchedulerService`
says so).

In every case it keeps the full error details for the host's log. Beyond the first place's own
catch-all, a command catches only the errors it expects by name, and only to turn them into a
refusal the member can act on, or to hand them to `report_failure` itself with context or an
outcome, as `/test-mode backup save` does with a backup fault, saying that the previous backup is unchanged.

*Rejected:* narrowing the capacity guards' catch to the errors they expect by name, in the
placement guard or the season's own capacity checks alike: a fault of any other kind would then
block a placement, a round or a season, when a check that cannot run must not.

*Rejected:* treating a calendar gate that cannot be read as a picture that could not be drawn: it
would add a line to the log channel and refuse `/division calendar-sync` for a fault in reading a
switch.

*Rejected:* treating a lineup gate that cannot be read as a picture that could not be drawn: it
would add a line to the log channel for a fault in reading a switch.

*Rejected:* narrowing the artwork signature's catch to the errors it expects by name: a fault of
any other kind would empty the whole fingerprint, and the review would read as changed in every
area.

**A refusal is raised and caught by name.** A refusal whose words the refusing code writes is raised
as `Refused` (`core/models/refusal.py`), its text what the member reads, or as a refusal type of its
own, caught by name; a refusal the command words itself from fields the error carries has a type of
its own, not a `ValueError` (`PackRefused`). A catch that turns a refusal into a reply names its
type around the call that refuses, and its own lines hold no parse of a stored value (a payload's
ids, a row) and call no hook, directly or through what they call: a save that calls a hook judges
inside the catch and calls the hook after it. A parse inside the call that refuses is harmless, as a
catch that names a refusal's type cannot take a parse's `ValueError`. Outside the exceptions below,
no refusal catch takes a bare `ValueError`: it is a fault, which Python raises for a bug (`int()` of
a stored id, a bad enum value read from the database). `Refused` subclasses `ValueError` so that a
caller with no cause to tell the two apart still catches it as a `ValueError`.

Three exceptions. Where a command, or a change's check or save, parses what the member typed (a
round's format), a catch may take that parse's own `ValueError` as the refusal, its try holding the
parse alone. The driver state machine's refusal of a transition the table does not allow is
`InvalidDriverTransition`, a fault that a save expecting the race catches by name. And `/bot pack`
is the one catch around a hook: its refusals are judged in the transaction that clears the server,
so that no change can start between the judging and the clear, and the modules' hook is called in
that transaction after them; `PackRefused` is raised by `pack_service` alone, so no hook's fault can
be taken for it (`pack_service.pack` says so).

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
  handlers (the four handler files are passed over, `POSTING_ALLOWED`, as is a function named in
  `POSTS_OFF_THE_QUEUE`), a post made outside a job by a function that is neither decorated nor
  named off the queue with its reason (`POSTS_OFF_THE_QUEUE`, which fails too on an entry with no
  reason or no function; the router is exempt, `JOB_CHECK_ALLOWED`, and `NOT_A_POST` names the two
  calls that are not posts, under "Every post is made in a job"), a service or helper reaching the
  change queue through the bot, a service looking another service up on the bot (a helper the cogs
  use may, as a cog does; `league_guild` is the one a service calls), a table written by a module
  that does not own it, a table read outside its owner (core's tables too; the scan sees tables, not
  columns, so a module's read of its own columns on a core table is named for good,
  `READS_OF_OWN_COLUMNS`), a module's own column on a core table named in a file outside that
  module, the entry point's included (`KNOWN_OWN_COLUMNS_OUTSIDE_THE_OWNER`; the check counts the
  column's name in any string outside a docstring, in SQL or as a row's key), core reading a
  module's service on the bot (an attribute `LeagueBot` types from a module's package, read from a
  file of core), SQLite's clock in a statement, a service reading the wall clock (the change queue's
  own default clock is the one place allowed, `WALL_CLOCK_ALLOWED`), a catch-all handler outside the
  five places (one that raises again, calls `report_failure` as a statement directly in its body, or
  sits directly in the body of a `for` loop, is in a place by its shape; `CATCH_ALLS_ALLOWED` names
  for good the failure paths of place 1, the stated exceptions, place 3's guards around reporting
  and writing a log line and the fifth place's handlers), and a save outside a queued change. The
  job check and the check of cogs handling their own errors are absolute, with no list of breaches
  left to grow, and the log channel's check holds its six places for good; every other check lists
  today's breaches, each under the work that removes it. The check of a save outside a queued change
  counts each function's `.commit()` and `.executescript()` calls, and a function writing on a
  handed connection commits nothing and is not counted. It allows for good the migrations run at
  start-up (`run_migrations` and `_enable_wal`, the journal mode set on the database file itself),
  the queue's own records, the retry queue for log lines, `/bot pack` (above), and
  `backup_service._write_empty_database`, which writes the scheduler's empty file and not the
  league's. The connection-state check refuses, outside the database code, a SQL function added with
  `create_function`, an authoriser, an assignment to `row_factory` or `isolation_level`, and a
  PRAGMA, ATTACH or temporary table
  (`test_a_lent_connection_carries_only_what_the_database_code_sets`); the few functions that set up
  a connection of their own, opened with `sqlite3.connect` and never lent, are named for good.
- **`tests/repository/test_one_league_server.py`**, **`tests/core/test_schema_rules.py`** and
  **`tests/repository/test_import_roots.py`** check the one-league rule, the schema's own rules, and
  that the bot is imported from one place: the installed package, never through `src`. The schema's
  checks, that every table has an owner, that every column a module keeps on a core table is in the
  schema, and that no column's default is SQLite's clock, live in `test_schema_rules.py` and read
  the schema the migrations build, not a migration file's text; the rules they hold are under "The
  database" and, for the clock, "The time is always passed in".

How the checks run, and how to work with the lists of today's breaches they hold, is CLAUDE.md's,
under "Testing".

Not every rule has its check yet. The rules about how things are built (the hooks, the one builder,
start-up, the sweep) cannot all be checked by reading the code; the rule that a refusal is raised
and caught by name ("Errors and failures") has no check yet.
