# The bot's architecture

This file says how the bot's code is meant to be laid out, and why. It covers what applies to
every module. Each module's own design file (`core.md`, and `results_module.md` and the rest)
covers that module only, and points back here instead of repeating anything.

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
(`LeagueBot`) keeps a typed attribute for each service, as it does today. All of them are
built by one function, which a test can call to get the real set of services. A service is
fully set up once it is built: nothing is wired in afterwards.
*Rejected:* a separate container object holding the services, since the services still need
the bot itself for Discord, so it would add a second object without removing the first.
*Rejected:* leaving things as they are, since hand-made fake bots in tests have picked the
wrong code path before (#240), and a service wired in two steps failed between them (#228).

**2. No shared base class for cogs, and no cog handles its own errors.** Every command's
failure already reaches `report_failure` through the command tree. If a cog handled its own
errors, the tree would step aside and `report_failure` would never run. A test stops that.
*Rejected:* a `LeagueCog` base class. It would hold two lines of code, and an error handler
added to it would switch off the one failure path for every command.

**3. Code is grouped by module.** There is one folder per module: core, results, attendance,
signup, weather, image, and stewarding once it is built. Each holds all of that module's code:
its cogs, services and models. Shared plumbing (the database connection, the base classes,
the log channel writer) belongs to core. Which module owns a file is then plain from where it
sits.
*Rejected:* grouping by layer first (`services/results/`, `cogs/results/` and so on), which
spreads one module over four folders. *Rejected:* grouping only the services. *Rejected:*
staying flat with a corrected list of which file belongs where, which is what failed before.

**4. The move into module folders is its own issue**, done straight after #282 and before
#283, so that every module pass starts from the new layout.
*Rejected:* each pass moving its own module, which leaves the code half-moved for weeks.
*Rejected:* doing it inside #282, which would bury the decisions in a mass rename.

**5. Everything lives in one package, `leaguebot`.** It is installed into the virtualenv
through a `pyproject.toml`, and the bot starts with `python -m leaguebot`. Module folder names
like `results` or `image` are then never confused with an installed library of the same name,
which is the kind of fault #398 was.
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
described as what it is, the one writer of the log channel.
*Rejected:* one gateway for every post, which would need to know every module's rules.
*Rejected:* one handler per channel, which repeats the same rules a dozen times.

**9. A failed post that the bot later replaces is retried by its owner.** The retry queue
records what the post is, and the retry asks the owning module to post it again. The text is
then written at the moment it is sent, and the owner remembers the new message as usual.
*Rejected:* the queue re-sending its stored text and writing the new message's id back itself.
*Rejected:* no longer retrying these posts.

**10. The rules are checked by import-linter and by our own tests.** import-linter checks
which code may import which. Our own tests check the rest. Where today's code breaks a rule,
each breach is listed with the issue that will fix it, and the list may only shrink: a check
fails on a new breach, and also on a listed breach that has since been fixed but not crossed
off.
*Rejected:* our own tests alone.

**11. Modules depend on core, never the other way round.** Core reaches a module only through
a few fixed hook points, which modules sign up to. Each module turns itself on and off. Which
module needs which is written down once, in one table.
*Rejected:* only writing the dependencies down as a table, which leaves each module's
switch-off code in core. *Rejected:* registering a module object for each module, more
machinery than six modules need.

**12. Timed work and restarts work the same way across the whole bot**, the way
`steward_module.md` §3–4 already designs them for stewarding. The database records when each
thing is due. A scheduled job only wakes the bot up, and the code it runs checks the database
again. When a command's change is saved, what it still has to post is saved with it. After a
restart, one sweep picks up whatever was missed or left half-done. Existing modules move over
to this through tracked issues.
*Rejected:* using this shape for stewarding only, which leaves the bot with three different
ways of recovering. *Rejected:* reporting half-done work for a league manager to repair by
hand instead of finishing it.

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

### Decided as usual good practice

These were not put to the owner as questions, being the usual practice; the owner approved
them with the plan for #282.

- Database code lives only in services, never in cogs or in the start-up code.
- A settings change and its audit record are saved together, or not at all.
- The bot never waits on Discord while it holds the database for writing (#155).
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
- import-linter runs as part of the normal test run.
