# Stewarding module — why the code is shaped as it is

Written before the module is built, from `docs/wip-specs/steward_module_specification.md` and the
38-issue breakdown in `steward-module-issues.md`. It holds the shape and the trade-offs, and no
functional rule: where a rule is at stake it is cited by its `[STW-…]` ID and left where it lives.
Corrected at close-out when building shows a decision was wrong.

The module is large — some 899 rules, 53 commands, three kinds of ticket, a licence that outlives
every season — and it is built in fourteen waves. Most of what follows is chosen for what it costs
the wave order, not for what it costs one issue.

## The shape

The cog surface is thin and the work is in services, as the rest of the bot is: `attendance_cog` is
some 600 lines against `attendance_service`'s 1,942, and that ratio is the one to keep. A steward
command body is a guard, an unpack, one call and one reply.

Services, and the issue that brings each:

| Module | Brought by | What it holds |
|---|---|---|
| `steward_licence_service` | S01 | the sanction record, the tallies, merges, expiry |
| `steward_config_service` | S02, S03, S05, S07 | the module's settings, its channels, its roles |
| `steward_team_service` | S04 | the team, the head, the effective team of a ticket |
| `steward_outcome_service` | S06, S26 | both outcome tables and their validation |
| `steward_auto_rule_service` | S08, S19 | the rules as configuration, then their triggering |
| `steward_ticket_service` | S10 | the ticket record, its channel, its parties, its buttons |
| `steward_ballot_service` | S11 | casting, counting, the tie-break, the justification |
| `steward_cycle_service` | S12–S17 | the stages, their clocks, and the close |
| `steward_conduct_service` | S28 | the conduct cycle, over the ticket framework |
| `steward_verdict_service` | S30, S31 | the shaping of every verdict this module issues |
| `steward_sheet_service` | S33, S34 | the licence sheet |
| `steward_message_service` | S03 | what the module has posted, and where |

`steward_verdict_service` lands in wave 7, two waves before the report cycle that first needs it,
and `steward_ballot_service` in wave 6 ahead of it. The seam that makes that order work is that
**S11 settles a verdict and S30 publishes it**: the ballot service writes a settled verdict row and
stops, and nothing before S30 reads it. The same seam holds between S16 and S19 — the close calls
an auto-rule hook that is a no-op until S19 fills it in, rather than S16 and S19 being one issue.

`tools/coverage_by_module.py` must gain a `steward` bucket, and the `"steward"` pattern must come
out of the `results` bucket where it sits today. First match wins in `RULES`, so otherwise the
whole module's coverage would be counted as the results module's — which is exactly the failure
issue #208 added the per-module gate to make visible.

---

## 1. The sanction record

**One table of sanction events, `steward_sanctions`, one row per instance. No counters.**

A licence is asked five different questions and only an event table answers all five. It must name
the ticket or ruling each active point and ban came from [STW-LIC-011]; it must revoke "the active
ones received most recently" [STW-REV-008, STW-REV-011, STW-REV-014], which is an ordering over
instances; it must carry an expiry frozen at the moment of receipt, since a change to the expiry
rule governs only what comes after it [STW-SET-012] and a merge carries each point with its own
date and expiry [STW-CON-010]; it must distinguish annulled from lifted, where the first counts
towards no tally and the second towards the lifetime ones alone [STW-REV-003, STW-REV-004]; and it
must hold the championship record as given, season and division and all [STW-CON-005]. A counter
column can express none of that, and a counter that drifts from the history behind it cannot be
repaired.

Every tally is therefore a query — the active ones, the lifetime ones [STW-CON-006], and the ban
history [STW-CON-003]. Nothing is stored twice. A licence holds a handful of rows to tens over a
career, so the cost of computing rather than storing is not worth measuring.

*Rejected:* counters on a licence row, for the reasons above. *Rejected:* a table per kind of
sanction — twelve near-identical tables, and every reader that walks "each of warning points,
penalty points and discipline points" [STW-LIC-008, STW-SHT-011] would union them.

**One row per unit for the countable kinds; one row per award for the measured ones.** An outcome
giving three warning points writes three rows sharing a source ticket and an expiry. Revoking two
of them is then an ordered update over rows rather than arithmetic on a counter that has to be
reconciled against a history it no longer matches. Time penalties and championship points
deductions are magnitudes rather than counts — an auto-rule counts them as the sum of their seconds
and of their points [STW-ARL-047] — so those carry a `magnitude` and are one row per award. The
rule is mechanical: if a rule can ask "how many", it is one row each; if it can only ask "how
much", it is one row with a number on it.

**A state column rather than a set of flags**: `ACTIVE`, `EXPIRED`, `SERVED`, `LIFTED`, `ANNULLED`,
with the moment it entered that state. Active is a state and not the absence of the others, so
[STW-CON-073]'s "yet to be served, yet to expire, or yet to be revoked" is one predicate and not
four. An annulled row is kept and excluded from every read rather than deleted; see the open
questions.

**The expiry is resolved and written at receipt, never recomputed.** Three shapes, because the
league chooses among three [STW-PEN-017]: a date, a season, or a count of rounds in a named
division. The round count is the awkward one, and it is mutable on purpose — it is the sanction's
own clock, decremented at the close of each qualifying round, and a round that does not happen does
not decrement it [STW-PEN-009]. That single mutable counter is also what makes the module's
disabled state cheap: nothing decrements while the module is off [STW-BAN-012, STW-BAN-015], so the
count resumes where it stood with no bookkeeping of the gap.

**The auto-rule flip-flop is not a property of a sanction and does not live on one.** It is a latch
per driver per rule — armed or not for the three flip-flopping types, and the highest multiple
already awarded for the historical one [STW-ART-008..011]. `steward_auto_rule_arming` holds it.
It is created by S01 rather than S08, because a merge must record a threshold crossed on either
licence as crossed on the merged one [STW-CON-014] and S01 owns the merge; it sits empty and unread
until S08 defines a rule and S19 checks one.

**A team penalty is one row per full-time driver on the ticket**, marked as the team's and carrying
the team, because that is what the licence records [STW-CON-005]. Revoking it moves every one of
them together [STW-REV-035], keyed by the source ticket and the team.

None of this is cheap to reverse. It is the table every other issue writes to and the only one in
the module whose shape a later issue cannot route around.

---

## 2. Tickets, ballots and their state

**One `steward_tickets` table with a `kind` column, not three tables.** S10 exists as its own issue
precisely because reports, appeals and investigations share a frame: the channel, who can see it,
the five buttons and their approvals, the effective stewarding team, the stage clock, the backup,
the deletion of the channel, the steward log. Three tables would mean writing that frame three
times or reading it through a union view, and would let the appeal and conduct cycles inherit a
frame shaped around reports — which is the outcome the issue breakdown says it is avoiding.

The per-kind surface is small enough not to be worth a side table. A report and an appeal both need
the round, the session and the lap — an appeal's ballot offers the outcomes of the original
report's session [STW-CYC-097] — so those sit on the ticket row and a conduct investigation leaves
them null, which is what its verdict output does with them anyway [STW-VER-015]. An appeal adds a
self-reference to the report it appeals and the tokens it cost. That is two columns, and a join for
two columns buys nothing.

**Parties are rows, not a list.** `steward_ticket_parties` carries the driver profile, the account
the driver was named under [STW-CON-008], the role on the ticket, when they joined, when they left
and why. It has to be rows: the header message is edited on each change [STW-TKT-046], the count is
capped at 25 [STW-CON-043], a removed party's row is what the steward log and the header history
read, and every ballot generates a line per party "in the order they are listed upon the ticket"
[STW-VER-039]. The effective stewarding team is the same shape and for the same reasons
[STW-TEM-011, STW-TKT-030..035].

**Channel permissions are per-member overwrites, not a role per ticket.** A mute takes writing and
attaching from one named person and leaves reading, voting and requesting exclusion intact
[STW-TKT-039], which a role cannot express without a second role per ticket, and the bot would then
be creating and destroying roles at the rate tickets are lodged. The party and steward rows are
what the overwrites are computed from, so the database stays the authority and Discord the display
— the same relation the rest of the module keeps.

**A ballot is a header row and a row per line.** `steward_ballots` carries the steward, the
justification, the decision where the ticket is an appeal [STW-CYC-096], and `confirmed_at`, which
is the moment [STW-DEL-030] orders by. `steward_ballot_lines` carries one row per involved driver
and per team line, with its outcome and its infringement.

The alternative was a JSON blob per ballot, and it is tempting because a ballot is counted whole
[STW-DEL-017] and a canonical string is the obvious thing to group by. It is rejected because the
lines are read individually after the count — the infringement most often given each driver
[STW-VER-039], every ballot written to the log in full once the deliberation closes [STW-TKT-004] —
and because a line references an outcome that may have been added mid-deliberation [STW-SET-006],
which is worth a foreign key. Counting whole is then done in Python: build a key from each ballot's
sorted lines and group. That runs over at most a dozen ballots, once, at the close.

**Hiding a ballot until the close needs no mechanism, only a discipline.** [STW-DEL-018] is kept by
there being exactly one function that aggregates across stewards, and by that function being called
only from the close. A test names it and holds that nothing else does; the temptation to show a
live tally in the ticket channel is real and would be a one-line change without one.

**Removing a vote deletes the rows.** [STW-DEL-021] says it shall be as if the steward had never
voted, and the log has already recorded that a ballot was cast and withdrawn [STW-TKT-004], so
there is nothing a soft delete would preserve that is not already kept elsewhere.

---

## 3. Scheduling, restart, and downtime

**The database holds the moment; APScheduler holds the alarm.** Every stage boundary is a column —
`stage`, `stage_started_at`, `stage_due_at` on the ticket for the per-ticket stages, and the same
three on a `steward_cycles` row for the boundaries a whole round crosses together [STW-CYC-053].
The scheduler job exists only to wake the bot up; its handler re-reads the row and acts only if the
row still agrees. A job that fires early because its window was lengthened does nothing and re-arms.

That inversion is the whole reason the downtime rule can be built in wave 12 without reworking
waves 6 to 11. If the fire time lived only in the job store, [STW-RST-002] would mean rewriting
every armed job of every open cycle on start-up, and S36 would have to reach back into S12, S13 and
S15. As columns, the extension is an `UPDATE` and the jobs re-arm from what it wrote.

Jobs go into the existing `SchedulerService`, with ids in this module's own prefix namespace so
that `cancel_round(only=…)` can take this module's jobs and no other's — the mechanism
`_WEATHER_JOB_PREFIXES` exists for, and the mistake issue #117 was. They are armed with no misfire
grace, unlike every other job in the service: [STW-RST-001] requires what came due during a stop to
be carried out on start, and the service's 300-second default would discard a deliberation close
that fell in a six-hour outage.

**Order is the start-up sweep's, not the scheduler's.** APScheduler fires everything due at once
and concurrently, which is not "in the order it would have happened" [STW-RST-001]. So the sweep
runs before the scheduler starts: it reads the module's own rows, applies the downtime extension,
then walks what is still due in ascending order of its moment and re-arms the rest. It is also
where a cycle close waiting for a repaired channel is tried again [STW-RST-004].
`_recover_rsvp_views_and_deadlines` in `bot.py` is the precedent, down to containing each failure
so that a start-up cannot be taken down by one unreadable row.

**Downtime is measured by a heartbeat, because nothing measures it today.** The bot writes
`last_seen_at` on a timer and at a clean shutdown; the gap on start is `now - last_seen_at`. A
gateway cut with the process alive is the other half of [STW-RST-002] and is recorded by
`on_disconnect`/`on_resumed` into the same place, so the sweep has one thing to read.

*Rejected:* deriving the gap from the jobs that missed their fire time. It only sees boundaries
that fell inside the gap, and the case the rule is mostly about is a window that merely *contained*
it — a defence period open across an outage, whose end has not yet arrived and must still move.

The extension is applied to every open window and to every later moment of the same cycle, and
recorded as a running total on the cycle so the league can be told why a round's verdicts came
later than the periods it configured. Clocks in which nobody acts are simply not in the set the
sweep touches [STW-RST-003]: a timed ban's expiry is a date, and the seven-day countdown to a
channel's deletion is a date, and neither is a window.

---

## 4. The cycle close

The close must be "applied entire or not at all" [STW-CYC-108] while posting to half a dozen
channels in the middle of itself. It cannot be one transaction, and the reason is not only issue
#155 — a transaction that spans an `await channel.send` holds SQLite's write lock across a network
round trip, which is #155's fault in its worst available form, and #155 records that no block in
the codebase does this today. It would also promise something it cannot deliver: a rollback does
not unsend a message.

So the close is a gate, one transaction, and a resumable tail.

**The gate is read-only and reuses a pattern that exists.** Before anything is written, every
channel the close will post to is checked against the gateway cache: it is there, it is a text
channel, and the bot can post in it. That is exactly `results_post_service.repost_channel_faults`,
written for issue #187 for the same reason — an amendment that overwrote a season's points and then
discovered a deleted channel. Copy the shape, not the code; the faults it returns are what the log
and the steward log name [STW-CYC-109].

**One transaction for everything that touches a licence.** Every sanction the round's tickets give,
and the auto-rule cascade that follows [STW-CYC-112, STW-ART-006], are one `get_connection` block
with one commit and no Discord call inside it. The cascade is pure computation over rows already
loaded — each rule triggered at most once per driver per close, so it terminates — which is what
makes a bounded transaction possible at all.

**Then the postings, outside it, and idempotent.** A `steward_cycle_closes` row carries a state:
checked, written, posted. [STW-CYC-111] is kept by not writing `posted_at` until the postings are
made; [STW-CYC-110] and [STW-RST-004] by the start-up sweep and the channel-setting commands
resuming from the state. Idempotence is what decision 7 is for: a resumed close knows what it
already posted because it recorded each message as it sent it.

The honest summary, and the one to hold in mind when reading [STW-CYC-108]: **the half that touches
a licence is atomic, and the half that touches Discord is gated, idempotent and resumable.** A
posting that fails after the licences are written leaves the close at `written` and does not roll
them back. Any other reading either holds a write lock across the network or promises to unsend a
message.

---

## 5. The command tree

Discord allows 25 children per top-level command and nesting two deep — a command, a subcommand
group, a subcommand. The spec's 53 steward commands fall into 14 subgroups under `/steward`
(`channel`, `role`, `team`, `report`, `appeal`, `justification`, `outcome`, `penalty`, `auto-rule`,
`backup`, `conduct`, `conduct-outcome`, `revoke`, `verdict`), none of them holding more than nine.
There is no pressure at either level and no third level is needed, so the review's K3 worry does not
bite.

**The group actually near its cap is `/division`**, which holds 15 children today and gains
`ticket-channel` and `licence-channel` [STW-CHN-002, STW-CHN-006]. Seventeen of 25. Worth noticing
before the next module adds to it.

**One `StewardCog`, because of how discord.py binds a group.** A subgroup whose `parent` is a group
declared outside its cog is not bound to that cog: the command is registered, `binding` is `None`,
and the callback is invoked without `self`. Verified against the 2.5.0 the Pi carries. So splitting
the 53 commands over several cogs by giving each cog a subgroup of a shared `/steward` group does
not work, and the alternatives that do are worse — several *top-level* groups (`/steward`,
`/steward-conduct`) contradict the command names the spec gives, and binding the groups by hand
fights the library for no gain.

The cost is one cog file with 53 command bodies in it. It is bounded by keeping those bodies thin:
a guard, an unpack, a service call, a reply. That lands the cog around 1,000 lines — larger than
`attendance_cog`, far smaller than `season_cog`, and the size lives in the services where the rest
of the bot puts it.

Commands that belong to other modules' groups go to the cogs that declare those groups, for the
same binding reason: `division ticket-channel` and `division licence-channel` to `season_cog`,
which owns `/division`; `module enable steward` to `module_cog`; `images template licence` and the
`licence` value of `images config toggle` [STW-SHT-017, STW-SHT-018] to `image_cog`; the three
test-mode commands [STW-TST-002, STW-TST-003, STW-TST-005] to `test_mode_cog`.

Two guards, not one. `utils.channel_guard` has `league_admin_only` and `league_manager_only`;
levels 3 and 4 [STW-MOD-018] need `head_steward_only` and `steward_only` beside them, checking the
team list rather than a role [STW-TEM-002] and checking the steward command channel rather than the
interaction channel [STW-CHN-012]. They belong in `channel_guard` with the other two, whose module
docstring is where the "two tiers" statement it corrects also lives.

---

## 6. Views, forms and custom ids

Everything derives from `LeagueView` and `LeagueModal`; `tests/unit/test_one_league_server.py` will
fail otherwise, and a persistent view answering its custom id on a server the league has left is
what those bases exist to refuse.

**`stw:<control>:<ticket row id>`, colon-separated, integer key.** The unique ticket ID is for
people and runs to 21 characters (`S12_D3_R22_001_APPEAL`); the row id is short and never changes.
A fixed separator that cannot occur in a component makes parsing a `split(":")` rather than the
`rsplit("_r", 1)` the RSVP scheme needs, which would break on a control name containing the
separator. The `stw:` prefix makes the module's controls recognisable at a glance and gives one
dispatcher something to route on.

**Only what is posted into a channel is persistent.** The five ticket buttons [STW-TKT-017], the
Vote button [STW-DEL-005], the tie-break buttons [STW-DEL-028], the justification accept and edit
buttons [STW-DEL-033], the backup retry button — which [STW-BKP-008] says must work through
restarts in as many words — the Report incident and Appeal incident buttons, and the approval
buttons in the steward command channel. Everything ephemeral — the ballot itself, every form, every
confirmation — is seen by one member, dies with the interaction, and must *not* be registered as
persistent.

Registration is one stub view per control family at start-up, as `RsvpView()` is registered, with
the ticket id read out of the custom id at callback time. The module carries no per-message state
that a stub cannot recover, so nothing needs the per-message `bot.add_view(view, message_id=…)` the
RSVP recovery does. `discord.ui.DynamicItem` would do the same with a regex and no stubs; it is a
pattern this repo has never used and the 2.5.0/2.7.1 divergence is a poor place to try one. Cheap
to swap later.

**Forms split by what they collect, not by how many fields they have.** A modal holds five text
inputs and nothing else — no choice, no member, no file, no read-only field. So:

*A private card* — an ephemeral message the bot edits in place, carrying selects and buttons, with
a Confirm that validates the whole — wherever the form has more than five fields or any field a
modal cannot hold. That is the ballot, `outcome add` and `modify`, the four auto-rule adds, and the
report and appeal forms. The ballot has no other option: [STW-DEL-012] requires the whole ballot to
stay in view while one line is changed, which a modal cannot do and an edited message does
naturally. The card also gives [STW-CYC-040] and [STW-CYC-086] — validate before the form closes,
keeping what was entered — for nothing, since the message is still there.

*A modal* where the form is five or fewer free-text fields and nothing else: the incident text
[STW-CYC-056], the grounds of appeal [STW-CYC-093], a justification for an exclusion or a request,
and `verdict republish`'s justification [STW-CYC-116] — whose other fields are shown rather than
asked for, so they are message text above the modal and not inputs in it.

**A half-filled card lives in memory and is lost on a restart.** Only a confirmed ballot, a
confirmed outcome and a filed ticket reach the database. Evidence attached before a ticket is filed
[STW-CYC-037] is held the same way, since the ticket is not filed until the evidence rule is met.
[STW-RST-001] asks that a ticket's ballots keep working, and a cast ballot is persisted
[STW-DEL-015]; it does not ask the bot to remember what somebody was halfway through typing, and
every other wizard in the bot loses that too.

**Choosing involved drivers is a user select, validated afterwards.** Discord's user select searches
members by typed name and scales to any server; it cannot be restricted to a division, so the
choices are checked against the division's seats after the fact and the refusal names who failed
[STW-CYC-032]. A string select of the division's drivers was ruled out: 13 teams of two already
exceeds its 25 options.

---

## 7. Posting and message identity

**Every message this module posts is recorded as it is sent, in one table.** Issue #189 exists
because the results module did not, and this module edits, deletes and reposts far more than that
one does: a verdict edited in place [STW-CYC-117], the default message deleted and rewritten
[STW-CYC-022, STW-CYC-024], the delay notice deleted when the verdicts land [STW-CYC-058], the
licence sheet's last message deleted [STW-SHT-002], a request message deleted on answer
[STW-TKT-047], the header message edited on each party change [STW-TKT-046].

`steward_messages` carries the ticket or round it belongs to, the division, a `kind`, the channel,
the message id, when it was posted, an `ordinal`, and when it was superseded. One table rather than
a column per kind spread over five tables, which is what #189 turned out to be.

**A row per message sent, with an ordinal, rather than an anchor and a scan.** A batch of verdicts
or a long licence sheet runs past Discord's 2,000 characters and becomes several messages.
`results_post_service._delete_with_continuations` stores the first and deletes the rest by walking
the channel for consecutive messages from the same author — which is a guess, and one that a
league manager posting between two chunks defeats. Recording each is exact, and costs nothing extra
because the send already returns the message.

This table is also what answers the ordering rules. [STW-VER-021]'s order within a batch, and
"until the next batch lands upon it" [STW-CYC-118..121] — which is what decides whether a verdict
may still be republished — are both readable from it without inspecting a channel.

It lands with S03, the channels, rather than with S30, the issue that first strains it. Nothing
before S03 posts anything, and retrofitting message identity after the postings are built is
precisely what #189 records the cost of.

**An attachment is re-fetched, not remembered by URL.** Discord's attachment URLs are signed and
expire, so a backup [STW-BKP-007] that downloaded from a stored URL would work in testing and fail
a week later. The evidence row keeps the filename and the message it was posted in; the backup
fetches that message and reads its attachments.

---

## 8. The schema change

The bot is not live, so every table and column above is added to
`src/db/migrations/001_baseline.sql` rather than to a new migration, as issue #254 settled. Each is added by the issue
that first needs it, not in one block up front: S01 the licence and sanction tables, S02 the config
row, S03 the channels and `steward_messages`, S04 the team, S06 the outcomes, S08 the auto-rules,
S10 the tickets, S11 the ballots and verdicts, S12 the cycles, S16 the close record, S36 the
downtime record.

The cost is that a developer's existing database has to be recreated at each such issue —
`run_migrations` will not re-apply a baseline it has already recorded, and refuses a database
recording a migration the bot does not carry. The test suite is unaffected: `tests/conftest.py`
keys its schema template on the set of migration files, so a changed baseline simply builds a new
template once.

**The trigger to stop is go-live, not the end of this module.** If the league starts running the
bot while the module is part-built, every remaining issue adds a numbered migration with a test of
its own instead, and the baseline is never edited again.

Naming follows what is there. Module settings go in `steward_module_config`, one row,
`CHECK (id = 1)`, with `module_enabled` on it — the shape `attendance_config` and
`results_module_config` use, rather than the column on `server_configs` that weather and signup
use. Per-division settings go in `steward_division_config` keyed by `division_id`, as
`attendance_division_config` is.

---

## 9. Testing the time-driven parts

No test may need a running bot, and the module is mostly clocks. Four rules make that workable.

**Every function that reads the clock takes `now`.** `datetime.now(timezone.utc)` is called at the
entry points alone — a command callback and a job callable — and passed down from there. The repo
already requires a test that pins a date to pin "now" alongside it, and `approval_window_service`
is the model: no database, no Discord, `now` a parameter, so a whole class of time arithmetic is
testable as pure functions. The downtime extension and the expiry resolution are both written that
way deliberately.

**A stage boundary is tested through its handler, never through APScheduler's clock.** Seed the
rows, call the handler with a fixed `now`, assert on what changed. What the scheduler is separately
tested for is that the right job is armed with the right id at the right moment — a real job store
in `tmp_path` and assertions over `get_jobs()`, as `test_scheduler_job_ids.py` does — and that each
module-level job callable survives its service or callback being absent, as
`test_scheduler_job_callables.py` does. The two halves are tested apart because that is the only
way to test either without a clock that runs.

**The start-up sweep is tested as `test_bot_rsvp_recovery.py` tests its own.** Build a database
with an open cycle whose boundary has passed and a `last_seen_at` an hour ago, run the sweep
against a stubbed guild, and assert on the rows and on what was posted. That single test shape
covers [STW-RST-001], [STW-RST-002] and [STW-RST-004].

**Discord is a `MagicMock`, and a test that builds a view is `async def`.** apt's 2.5.0 calls
`asyncio.get_running_loop()` in `View.__init__` where the pinned 2.7.1 defers it, so a sync test
that builds one of this module's many views passes on CI and fails on the Pi. A service takes
`db_path: str`; handing it a mock writes a file named after the mock, which is how stray
`<MagicMock name='mock.db_path' …>` files appear in a checkout.

What cannot be covered here is that Discord actually routes a persistent view's callback after a
restart, and that a card renders as intended. Both are full system testing, done by hand. The
test-mode commands S37 brings [STW-TST-001..006] are the harness for that, and are not a substitute
for the unit tests every issue carries.

---

## Questions the specification left open

All five were settled with the user on 2026-09-20 and written into the specification; each is kept
below, struck through, with what was decided and why. They are kept rather than deleted because the
reasoning is what a later reader needs when one of them is reopened.

**~~Whether an annulled sanction's row may remain.~~** Settled 2026-09-20: it remains, marked
`ANNULLED`, read by nothing — [STW-REV-038] now says so. A verdict already posted is never
corrected, so deleting the row would leave the channel and the licence disagreeing with nothing in
the database to explain the gap. The exclusion predicate is the cost, and it is worth a named test:
one query that forgets it resurrects a sanction that should not exist.

**~~How long a gap may be disregarded.~~** Settled 2026-09-20: five minutes, now named in
[STW-RST-002]. It is the interval the bot already holds to in six places — the scheduler's
300-second misfire grace, `APPROVAL_WINDOW_SECONDS`, the placements-review button, the retry
loop, the signup correction timeout and the signup view's own — so the downtime rule and the
misfire grace cannot disagree about one boundary. Below it, everybody's window is quietly
shortened by the outage; that is accepted.

**~~What the flip-flop latch of a historical-accumulation rule becomes on a merge.~~** Settled
2026-09-20, and [STW-CON-014] rewritten with a worked pair in [STW-CON-075]: what carries over is
what was **already awarded**, not what the merged tally implies. A crossing the merged licence
reaches that neither licence was sanctioned for fires at the next close, because the merged driver
is one person and their record is what it always was. Licences of 13 and 19 against rules at 20 and
30 therefore fire both; licences of 21 and 11 fire the 30 alone, the 20 having been awarded. The
same holds for the active-accumulation type, where two licences under the threshold can merge to a
tally above it: the latch is armed where either was, and an unarmed merged crossing fires.

The design consequence is that `steward_auto_rule_arming` is merged by taking the **highest awarded
multiple** of the two rows per rule, rather than by recomputing from the merged tally, and that the
merge is followed by an ordinary auto-rule check rather than a silent re-arm.

**~~What happens when a ticket's channel cannot be created.~~** Settled 2026-09-20 and written
into [STW-CON-046]: the ticket is not filed, the one who lodged it is told, nothing is written, and
the steward log records who and which round so the stewards can lodge it in their stead. Filing and
holding it channel-less was rejected for leaving a ticket nobody can defend inside a round's stages;
a shared fallback channel was rejected outright against [STW-CON-044]'s privacy.

The design consequence is that channel creation comes **before** the first write, not after it: the
ticket row, its parties and its evidence are written only once the channel exists, so the failure
path has nothing to undo.

**~~Whether a paused outcome may still be prefilled on an appeal ballot.~~** Settled 2026-09-20 by
closing the gap rather than reconciling the two rules: [STW-SET-007] now forbids modifying,
removing or pausing an outcome while an appeal *submission* is open as well as a deliberation, and
[STW-SET-010] does the same for switching off a penalty type, which empties the outcomes carrying
it. A paused outcome therefore cannot be one an open appeal must prefill.

The design consequence is that the refusal these two rules describe is one check over the module's
open stages, not a check per command: the stages are rows with a due moment (decision 3), so "is
any report deliberation, appeal submission or appeal deliberation open" is a single query, and both
rules call it.
