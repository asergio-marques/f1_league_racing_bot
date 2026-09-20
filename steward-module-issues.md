# Stewarding module — the issue breakdown

Drafted 2026-09-20 for review. **Nothing here is filed.** Each entry below becomes one GitHub
issue once you have been through it.

## How to read this

Every issue carries:

- **Labels** — `module-stewarding` (to be created) and `feature-request` on all of them, plus one
  priority.
- **Rules** — the requirement IDs it owns, from `docs/wip-specs/steward_module_specification.md`.
  Every one of the spec's 899 rules is owned by exactly one issue; the coverage table at the end
  proves it, and is generated from the spec rather than written by hand.
- **Depends on** — the issues that must land first, which is what gives the implementation order.
- **Blocked by** — work outside this module, filed or still to file.
- **What a league can see** — the observable result, so that the issue is finished by something
  other than "the code exists".

Each issue is meant to be one `/speckit-specify` increment. The unit tests that verify its
behaviour belong to the issue, as this repo requires of every change.

## The priority scheme

Nothing here is live, so CONTRIBUTING's "how much does a league lose" cannot rank these on its
own. They are ranked by what the module cannot do without:

- **Critical** — the module does not exist without it: the licence and the concepts as data, the
  module's own enable and disable, the channels, the stewarding team, the ticket lifecycle,
  deliberation, verdict output, the cycle close.
- **High** — every league meets it in the ordinary run of a season: reports, appeals, bans and
  their serving, auto-rule triggering, the licence sheet, standings, revokes.
- **Medium** — optional, off by default, or a second rendering of something that already works as
  text: CoC investigations, backups, every image output, the licence view, test-mode helpers.
- **Low** — a convenience.

## Prerequisites outside the module

Three of these exist; two would need filing before the issues that depend on them.

| Prerequisite | State |
|---|---|
| "Principal division" defined in core | filed, #278 |
| The base role and signed-up role moved to core | filed, #276 |
| A hub channel in core, with a panel each module adds to | filed, #279 — blocks S23 (#312) (#312) |
| Constitution: the licence is the ban state, and "Upheld" means the initial verdict stands | filed, #280 — blocks S01 (#290) (#290) and S15 (#304) (#304) |

The 21 cross-document edits the review file records are attached below to the issue that makes
each true, rather than left as a list.
---

## S01 — The driver licence and the module's vocabulary  ·  #290

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CON-001..008, STW-CON-040..041, STW-CON-055..067, STW-CON-073..074
**Depends on:** —
**Blocked by:** #278 (principal division), #280 (the constitution's ban state)

The licence is what every other issue writes to, so it comes first. This issue brings the record
itself and the concepts that describe what may sit upon it.

- The driver licence as a record of the driver profile: warning, penalty and discipline points
  with their dates and active state; qualifying, race, season and league bans, active and
  historical; appeal tokens; the championship record; the tallies.
- The licence as the state of a driver's bans, replacing the driver states removed in #221.
- What happens to a licence when two driver profiles are merged, and when another account becomes
  a driver's current one.
- The vocabulary the rest of the module uses: each kind of penalty, what an active penalty is, the
  principal division, and the feature race and feature qualifying.

**What a league can see:** nothing yet, by design — this is the data structure. It is observable
through S23 (#312) (#312)'s "View licence" and S33 (#322) (#322)'s licence sheet, which are the first things to read it.

**Owed document edits:** core's rule that a driver returning to Not Signed Up is deleted, which a
licence recording a sanction now prevents; core's "A driver shall not be banned", which should
name the licence's active bans as the bar; constitution Principle VIII.

---

## S02 — Enabling and disabling the module, and the four levels of authority  ·  #291

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CFG-001..002, STW-MOD-001..010, STW-MOD-015..016, STW-MOD-018..023
**Depends on:** S01 (#290)

- `/module enable steward` and `/module disable steward`, off by default, enabled only before a
  season's placements are confirmed, disabled only while no cycle stands open.
- The dependency upon the results & standings module, in both directions: stewarding cannot be
  enabled without it, and disabling it cascades.
- Turning the results module's own penalty and appeal reviews off while stewarding is enabled.
- The two new levels of authority, 3 and 4, independent of the league admin and league manager.
- Every configuration change written to the log channel, and every stewarding-team command
  written to the steward log channel.

**What a league can see:** the module turns on and off, appears in the configuration and
placements reviews, and refuses with a reason where a cycle is open.

**Owed document edits:** core's "Two tiers of authority shall govern every command"; the results
spec's disabling rule.

---

## S03 — The module's channels  ·  #292

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CHN-001..015
**Depends on:** S02 (#291)

`division ticket-channel`, `division licence-channel`, `steward channel conduct-verdicts`,
`steward channel command` and `steward channel log`, the inherited `division verdicts-channel`,
the one-purpose-per-channel rule, the placements and configuration review refusals, and the rule
that a command is given in the channel of the level it is given under.

**What a league can see:** the channels can be set, are shown in the placements review, and a
season cannot be confirmed while one is missing.

**Owed document edits:** core's "three channels for the server and eight for each division", which
becomes six and ten; the README's two counts of eight.

---

## S04 — The stewarding team, its roles and its head  ·  #293

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CON-021..029, STW-TEM-001..018
**Depends on:** S02 (#291), S03 (#292)

- The team as a list the bot keeps, with the roles mirroring it.
- `steward team add`, `remove`, `list`, `head-assign`, `temp-head-assign`, `temp-head-remove`, and
  the three role commands.
- The head steward and the temporary head steward, and what a replacement does to open tickets.
- The effective stewarding team and the conflict-of-interest toggle.
- Placements refused while no head steward is appointed.

**What a league can see:** a panel of stewards with roles granted and revoked by the bot, and a
season that will not start without a head steward.

---

## S05 — Timings, appeals and justification configuration  ·  #294

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-APL-001..006, STW-APL-008..009, STW-JUS-001..003, STW-JUS-008..015, STW-TIM-001..018
**Depends on:** S02 (#291)

The five period commands with their defaults and their 168-hour cap; the appeal toggle and its
refusal where the cap would be breached; the appeal token pair and the refund toggle; the
justification final and fallback modes, other than the LLM mode, which is S38 (#327) (#327).

**What a league can see:** every timing and appeal setting can be set and read back, and an
invalid combination is refused with the sum named.

---

## S06 — Adding and modifying an outcome  ·  #295

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CON-053..054, STW-OUT-001..026
**Depends on:** S02 (#291)

`steward outcome add`, `modify`, `remove`, `toggle` and `list`; the form and every field it
collects; the 25-per-session limit; the permanent NFA outcome; the rule that an outcome sets at
least one penalty; and what a paused outcome does.

**What a league can see:** a league's own penalty table, listed on demand, ready for a ballot to
offer.

---

## S07 — Penalty types, expiries and the ban configuration  ·  #296

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-PEN-001..024
**Depends on:** S01 (#290), S06 (#295)

`steward penalty toggle` over the twelve penalty types, with its refusals where an outcome would
be left empty; the season ban role and type, in its three forms; the warning and penalty point
expiries; the league ban role.

**What a league can see:** a league decides which penalties exist and how long they last, and is
refused where turning one off would empty an outcome.

---

## S08 — Single-round and multi-round auto-rules  ·  #297

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-ARL-001..024, STW-CON-068..070
**Depends on:** S07 (#296)

The four `steward auto-rule add-…` commands and their forms, `modify`, `remove`, `toggle` and
`list`; which types may be counted and handed out; the rule that an auto-rule judges one licence
and never a team; and where the penalties it hands out land.

**What a league can see:** rules like "three penalty points in a round means a race ban" exist and
can be listed, though nothing triggers them until S19 (#308) (#308).

---

## S09 — Changing settings during a season  ·  #298

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-SET-001..012
**Depends on:** S05 (#294), S06 (#295), S07 (#296), S08 (#297)

What may be changed once placements are confirmed and from when each change governs: a cycle
already open finishes under the settings it began with, an outcome may be added mid-deliberation
but not modified, and an auto-rule judges only what happens after it exists.

**What a league can see:** a mid-season change takes effect at the next round's cycle, and the
stewards are told in the command channel when an outcome appears mid-deliberation.

---

## S12 — Report submission, and the ticket a report opens  ·  #301

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CON-031, STW-CON-039, STW-CON-042..048, STW-CON-050, STW-CYC-022..043, STW-TKT-001, STW-TKT-016..017, STW-TKT-048..051
**Depends on:** S03 (#292), S04 (#293), S06 (#295)

The report side of the cycle, and the ticket record every kind of ticket is built on — the framework that was S10, landing where it first becomes exercisable.

- The "Report incident" button, its window, the report form and its evidence, the ID format, and a steward's report as against a driver's.
- The channel the bot creates for the ticket, who can see it, and what happens when it cannot be created: the ticket is not filed, the lodger is told, and the steward log records it.
- The permissions each party and each steward holds while a ticket is open to its parties, and that the phase cannot be ended early.

**What can be exercised:** a driver reports an incident after a race and gets a private channel holding the complaint, the evidence and the drivers involved.

---

## S13 — Report deliberation and the ballot  ·  #302

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CON-033, STW-CYC-055..058, STW-DEL-001..016, STW-DEL-018..021
**Depends on:** S12 (#301), S51 (#341)

The deliberation stage and the ballot itself — what was S11, landing where a steward can first use it.

- The deliberation period, and the incident as the verdict will describe it, settled by the effective head steward.
- The ballot: a line per involved driver, a line per team that may be sanctioned, one justification for the whole, and which teams may be sanctioned at all.
- Casting, changing and removing a ballot, hidden from every other steward until the deliberation closes.

**What can be exercised:** stewards discuss a report in its channel and vote on it, each seeing their own ballot and nobody else's.

---

## S14 — Appeal submission and the appeal tokens  ·  #303

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-APL-007, STW-APL-010..011, STW-CON-034, STW-CON-049, STW-CON-051, STW-CYC-065..091
**Depends on:** S49 (#339), S05 (#294)

Who may appeal and when, the cost in tokens and the refund where an appeal succeeds, the appeal
form and its ID, one appeal per report, the appellant as an involved driver of their own appeal,
and the cycle closing where no appeal is lodged.

**What a league can see:** a driver appeals a verdict, is charged a token where the league
charges, and gets it back if the stewards change the verdict.

---

## S15 — Appeal deliberation  ·  #304

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-CON-035, STW-CYC-092..107
**Depends on:** S14 (#303), S48 (#338), S49 (#339), S30 (#319)
**Blocked by:** #280 (the constitution's appeals lifecycle)

The appeal ballot with its Uphold or Change decision, lines prefilled from the initial verdict and
greyed out until the decision changes, the grounds of appeal as settled, the appeal verdicts
posted in the order of the reports they appeal, and the results and standings reposted as Final.

**What a league can see:** an appeal is decided, and where it changes the verdict the change is
shown against what the report gave.

**Owed document edits:** the constitution's appeals lifecycle — "Upheld" must mean the initial
verdict stands.

---

## S16 — The cycle close  ·  #305

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CON-030, STW-CYC-108..115
**Depends on:** S49 (#339), S15 (#304), S07 (#296), S32 (#321), S33 (#322)

The close applied entire or not at all: every channel checked before anything is written, then
points and bans written to the licences, the auto-rules checked, the sheets reposted, and the
close held at a channel that cannot be posted to until it is repaired.

**What a league can see:** at the end of a round's stewarding, licences and sheets move together,
or nothing moves and the league is told which channel is at fault.

---

## S17 — The cycle moves the round, and cancellation  ·  #306

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CYC-001..021
**Depends on:** S12 (#301), S49 (#339), S15 (#304), S16 (#305)

The round's states driven by the cycle rather than by the results module's reviews; the labels the
results and standings carry as the round moves; a round whose cycle is open refusing cancellation,
and a division or season cancelled meanwhile waiting for it; a round cancelled with its sessions,
whose tickets close without a verdict; and the module's own cancellation notice.

**What a league can see:** the round's state follows its stewarding, and a cancellation is
explained in the ticket channel rather than leaving tickets unexplained.

**Owed document edits:** core's round states and the results spec, on which module moves a round;
core's cancellation of a division or season with a cycle open.

---

## S18 — Republishing a verdict  ·  #307

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-CYC-116..123
**Depends on:** S30 (#319), S49 (#339)

`steward verdict republish`, editing a verdict's justification in the message it was posted in,
only until the next batch lands upon it, and the rule that no command corrects an outcome.

**What a league can see:** a justification with a mistake in it can be corrected without the
verdicts channel losing its order. Subsumes #189.

---

## S19 — Auto-rule triggering  ·  #308

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-ART-001..011
**Depends on:** S43 (#332), S16 (#305), S30 (#319)

Rules checked at a cycle close and after a CoC verdict, the Automated Ruling published for each,
the ID formats, one rule triggering another until none is left, and the flip-flop that keeps a
threshold from punishing twice.

**What a league can see:** "three penalty points means a race ban" happens by itself, announced as
an Automated Ruling.

---

## S20 — Season bans and league bans  ·  #309

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-BAN-001, STW-BAN-007..008, STW-BAN-047..065
**Depends on:** S01 (#290), S07 (#296), S16 (#305)

A driver returned to Not Signed Up with every seat freed; the ban roles; the bar on signing up, on
being placed and on checking in; season bans stacking and expiring by rounds, by a season's end or
by days; a league ban replacing a season ban and covering every account the driver owns; a driver
with a sanction never deleted.

**What a league can see:** a banned driver loses their seats and cannot sign up again until the
ban ends.

---

## S21 — Serving a qualifying ban or a race ban  ·  #310

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-BAN-002..006, STW-BAN-016..018, STW-BAN-023..035, STW-BAN-040..046
**Depends on:** S01 (#290), S20 (#309), S30 (#319)
**Blocked by:** #278 (principal division)

Which division a ban is served in, judging from the round's results whether it was served, the
Automated Ruling posted either way, a ban unserved carrying on, and a ban outliving its season.

**What a league can see:** a banned driver sits out the right round, and the bot says so publicly.

---

## S22 — Bans while the module is disabled  ·  #311

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-BAN-010..015
**Depends on:** S20 (#309), S21 (#310), S02 (#291)

Season and league bans staying in force with the module off, nothing expiring meanwhile, and every
count resuming where it stood when the module is enabled again.

**What a league can see:** turning the module off does not quietly free a banned driver.

---

## S23 — Viewing a licence, as text  ·  #312

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-LIC-001..011
**Depends on:** S01 (#290)
**Blocked by:** #279 (the hub channel)

"View licence" on the hub panel: any member names a driver and reads their licence, seen by them
alone — active points with their expiries, active bans with where each is served, the ban history,
the championship record, the tokens, and the ticket each entry came from.

**What a league can see:** the first window onto the licence, and the first proof S01 (#290) (#290) holds what
it should.

---

## S24 — Viewing a licence, as a graphic  ·  #313

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-LIC-012..020
**Depends on:** S23 (#312)

The licence drawn, dressed in the principal division's logo and colours, with a row per division
the driver races in.

**Owed document edits:** the image spec — a licence view graphic, with division fields addressed
by row, which no graphic declares today.

---

## S25 — What stewarding changes in the attendance module  ·  #314

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-BAN-009, STW-BAN-019..022, STW-BAN-036..039, STW-MOD-011..014
**Depends on:** S49 (#339), S20 (#309), S21 (#310)

Attendance pardons given by a league manager's command in place of the penalty review's button;
attendance points distributed when the report verdicts are posted; a race-banned driver's check-in
answer discarded, with an automatic pardon; a qualifying-banned driver told and reminded; and
reserves distributed again where a ban frees a seat.

**What a league can see:** a banned driver is not charged attendance points for a round they were
not allowed to enter.

**Owed document edits:** the attendance spec, on pardons and on when points are distributed.

---

## S26 — Code of Conduct investigations: the toggles and the periods  ·  #315

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-COC-001..012
**Depends on:** S02 (#291), S07 (#296)

The conduct toggle, off by default; who may start an investigation; the discipline point expiry;
the defence and deliberation periods; and the whole conduct outcome table, with its own NFA.

**What a league can see:** a league that wants conduct investigations can configure them, though
none can be started until S28 (#317) (#317).

---

## S27 — Backing up a ticket's channel  ·  #316

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-BKP-001..009, STW-COC-057..063
**Depends on:** S12 (#301), S28 (#317)

`steward backup report-toggle` and the JSON written to `./tickets`, with the attachments; a
channel not deleted where its backup failed, with a button to try again; and the warning when the
disk runs low.

**What a league can see:** a closed ticket survives its channel's deletion, on the machine the bot
runs on.

---

## S28 — The conduct investigation cycle  ·  #317

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-CCY-001..026, STW-CON-036..038, STW-CON-052
**Depends on:** S26 (#315), S50 (#340), S52 (#342), S12 (#301), S13 (#302), S48 (#338), S49 (#339), S30 (#319)
**Blocked by:** #276 (the base role moved to core)

`steward conduct start` and its form, the investigation's ID and channel, the defence and
deliberation, the verdict posted to the conduct verdicts channel and reposted to each division the
user races in, and the cycle close: outcomes made effective, a driver profile created where the
user has none, championship penalties held where no season is ongoing.

**What a league can see:** a member can be investigated for conduct outside a race, and the
verdict is public while the investigation was not.

---

## S29 — Revoking a penalty  ·  #318

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-REV-001..038
**Depends on:** S01 (#290), S20 (#309), S21 (#310), S32 (#321), S33 (#322)

The eleven revoke commands, each taking whether the sanction is annulled — as though never given —
or lifted, which keeps it on the record and ends it early; the refusals; the standings reposted
where a championship or team penalty goes; and the two commands a league admin keeps while the
module is disabled.

**What a league can see:** a mistake can be undone, and a ban can be ended early without pretending
it never happened.

---

## S30 — Verdict output, as text  ·  #319

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-VER-001..017, STW-VER-019..041
**Depends on:** S12 (#301)

Every verdict this module issues, shaped one way: its label, its ID, the round and session, a
decision line per involved driver and per team sanctioned, the incident, the justification, the
order a batch is posted in, its header, and the rule that a verdict pings nobody but the drivers it
names. Automated Rulings fill the same fields with what the bot writes.

**What a league can see:** the verdicts channel reads as a record of decisions, whatever the image
module is doing. Subsumes #246 and #204.

---

## S31 — Verdict output, as a graphic  ·  #320

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-VER-018, STW-VER-042..054
**Depends on:** S30 (#319)

The verdict graphic's fields, a row per driver and per team, drawn in the division's colours.

**Owed document edits:** the image spec's verdict graphic and banner, amended to carry this
section.

---

## S32 — Championship penalties in the standings  ·  #321

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-STD-001..005
**Depends on:** S01 (#290)

A points deduction marked by a footnote in the text and an optional column in the graphic; a
championship disqualification shown as "DSQ" beneath the classified; a penalty taking effect with
its verdict; and a CoC verdict reposting the standings it touches.

**What a league can see:** the championship tables show a deduction and say so, instead of a total
that looks wrong.

**Owed document edits:** the results spec and the image spec, on both standings.

---

## S33 — The licence sheet, as text  ·  #322

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-SHT-001..015
**Depends on:** S01 (#290), S03 (#292)

The sheet posted per division: points, outstanding bans and suspended drivers, ordered by active
points, reposted whenever a licence changes, and posted as an opening sheet when placements are
confirmed.

**What a league can see:** a division's standing in points and bans, at a glance, in its own
channel.

---

## S34 — The licence sheet, as a graphic  ·  #323

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-SHT-016..038
**Depends on:** S33 (#322)

The sheet as an image module aspect: its toggle, its template command, its fields and its
per-round column.

**Owed document edits:** the image spec's aspects list and `images config toggle`.

---

## S35 — Packing the bot  ·  #324

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-PCK-001..003
**Depends on:** S02 (#291), S03 (#292), S04 (#293)

A pack clearing the team, the roles and the channels, keeping every licence, and refusing while a
ticket is open.

**Owed document edits:** core's pack section, naming this module's channels and roles.

---

## S36 — Surviving a restart  ·  #325

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-RST-001..004
**Depends on:** S12 (#301), S13 (#302), S49 (#339), S15 (#304), S16 (#305)

Every scheduled stage carried out on start-up in the order it would have happened, buttons and
ballots still working, and downtime lengthening every window in which a user or steward acts.

**What a league can see:** a restart mid-cycle costs nobody their defence or their vote.

**Owed document edits:** core's "When the bot stops".

---

## S37 — Test mode  ·  #326

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-MOD-017, STW-TST-001..006
**Depends on:** S12 (#301), S49 (#339), S15 (#304)

`test-mode report lodge`, `test-mode appeal lodge` and `test-mode ballot cast`, fake drivers on
the stewarding team, and the stages stepped through with core's test-mode commands.

**What a league can see:** a maintainer can drive a whole cycle in minutes rather than days.

---

## S38 — The LLM justification mode  ·  #327

**Labels:** `module-stewarding`, `feature-request`, `Low`
**Rules:** STW-JUS-004..007
**Depends on:** S05 (#294), S48 (#338)

The third justification mode, available only where whoever runs the bot has connected an LLM, with
the fallback used where it fails and the warning that the stewards' justifications leave the
machine.

**What a league can see:** a verdict written up from every steward's reasoning rather than the
longest of them.

---

## S39 — Merging licences, and a driver's accounts  ·  #328

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CON-009..020, STW-CON-075..076
**Depends on:** S01 (#290)

The merge and the accounts half of the licence, split from S01 (#290) so neither is oversized.

- Two driver profiles merged into one: points carried with their own dates and expiries, bans added together, season bans stacked, a league ban taking precedence, the lower token count kept.
- An auto-rule already awarded upon either licence held as awarded upon the merged one, and a crossing the merged record reaches triggered at the next close.
- A driver's current account changing: the ban roles moving with it, an open ticket following the driver, a steward's vote keeping the account it was cast under, and a past account barred from the stewarding team.

**What can be exercised:** two profiles are merged and the surviving licence holds everything both held, with nothing escaped and nothing punished twice.

---

## S40 — The temporary head steward, and conflicts of interest  ·  #329

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-TEM-019..032
**Depends on:** S04 (#293)

The temporary head steward, split from S04 (#293).

`steward role temp-head`, `steward team temp-head-assign` and `temp-head-remove`: the appointment, the role that switches the feature on, what a replacement does to open tickets, and which commands the temporary head steward may use while the head steward keeps every one of theirs. Also `steward team conflict-toggle`, which decides whether a steward may judge a division they race in.

**What can be exercised:** a head steward going away for a fortnight hands the panel to someone else, and takes it back.

---

## S41 — Removing, pausing and listing outcomes  ·  #330

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-OUT-027..055
**Depends on:** S06 (#295)

The rest of the outcome table, split from S06 (#295).

`steward outcome remove`, `toggle` and `list`; what a paused outcome does and does not do; the 25-per-session limit as pausing and resuming move against it; and the permanent, unmodifiable No Further Action outcome every ballot offers.

**What can be exercised:** a league prunes its penalty table, pauses an outcome it is reconsidering, and reads the list back.

---

## S42 — Accumulation auto-rules  ·  #331

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-ARL-025..046, STW-CON-071..072
**Depends on:** S08 (#297)

The two accumulation rules, split from S08 (#297).

`steward auto-rule add-active-accumulation` and `add-historical-accumulation`, their forms and validation, and what each type means: a tally of active penalties against a threshold, and a lifetime tally crossing further multiples of one.

**What can be exercised:** a league configures "four active penalty points means a race ban" and "twenty over a career means a season ban", and reads them back.

---

## S43 — What an auto-rule counts and hands out, and managing them  ·  #332

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-ARL-047..066
**Depends on:** S08 (#297), S42 (#331)

What an auto-rule may count and hand out, and the commands that manage the list. Split from S08 (#297).

The countable and awardable types; the rule that an auto-rule judges one licence and never a team; where a time penalty, a disqualification or a championship penalty an auto-rule hands out actually lands; a disabled penalty type offered neither way; and `steward auto-rule modify`, `remove`, `toggle` and `list`.

**What can be exercised:** a rule can be corrected, paused, resumed or removed, and the whole set listed — and a rule that would hand out a switched-off penalty is refused.

---

## S44 — The steward log, and the stewarding team's one voice  ·  #334

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-TKT-002..013
**Depends on:** S12 (#301)

The steward log and the unified front, split from the ticket framework.

Every act in a ticket and every use of a module command written to the steward log — who, what, when, upon which ticket — with ballots held back until the deliberation closes and then written in full. And the rule the league sees: no message a driver can read attributes a decision, a vote or a request to any single steward, and only the effective head steward is ever named.

**What can be exercised:** a manager reads the steward log and can account for everything that happened to a ticket, while the drivers see one stewarding team speaking with one voice.

---

## S45 — Adding and removing a driver from a ticket  ·  #335

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-TKT-018..029, STW-TKT-043..047
**Depends on:** S51 (#341)

Adding and removing a driver from an open ticket, split from the ticket framework.

The Add driver and Remove driver buttons, and the three paths through each: the effective head steward acts, a steward asks and is approved in the command channel, or a party asks and is answered in the ticket. The refusals, the header message edited on each change, and the withdrawal of both buttons once deliberation begins.

**What can be exercised:** a witness with the footage is brought into a report, and someone named by mistake is taken out.

---

## S46 — Excluding a steward, and handing a ticket on  ·  #336

**Labels:** `module-stewarding`, `feature-request`, `High`
**Rules:** STW-TKT-014..015, STW-TKT-030..036
**Depends on:** S51 (#341)

Excluding a steward from a ticket, and handing it on. Split from the ticket framework.

The Request exclusion button: a steward steps aside, and the effective head steward hands the ticket to a named member who accepts or refuses. What happens when nobody accepts by the time deliberation is reached, and why a steward who has cast a ballot must remove it first. Also the effective head steward who is themselves a party or holds a conflict, and the bot's fallback choice.

**What can be exercised:** a ticket whose head steward is involved in the incident is passed to someone who is not.

---

## S47 — Muting in a ticket  ·  #337

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-TKT-037..042
**Depends on:** S51 (#341)

Muting in a ticket, split from the ticket framework.

The Mute and Unmute buttons: before deliberation they act upon the parties, during it only the effective head steward may use them and only upon stewards, and the effective head steward can never be their target. A muted steward keeps reading, voting and requesting exclusion.

**What can be exercised:** a ticket that has turned into an argument is quietened without anyone losing their vote or their defence.

---

## S48 — Counting the ballots, plurality and the tie-break  ·  #338

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-DEL-017, STW-DEL-022..030
**Depends on:** S13 (#302)

Counting the ballots and reaching a decision, split from deliberation.

A ballot counted whole, plurality over the ballots actually cast, the effective head steward's ballot deciding a tie they are part of, and where they are not: the delay message, a button per tied option, an hour to choose, and the option whose final count came earliest standing if that hour runs out.

**What can be exercised:** a deliberation closes and a decision comes out of it, including from a panel that split three ways.

---

## S49 — The justification, and a round's report verdicts  ·  #339

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CYC-059..064, STW-DEL-031..037
**Depends on:** S48 (#338), S30 (#319)

Settling the justification and publishing a round's report verdicts. Split from deliberation.

The default justification the bot offers by the configured mode, the hour the effective head steward has to accept or rewrite it, the other ballots' justifications shown for reference, and then the round's verdicts posted together in the order the reports were lodged — with the results and standings reposted carrying what they changed.

**What can be exercised:** a round's stewarding ends in verdicts a league can read, under the right label.

---

## S50 — Adding and modifying a conduct outcome  ·  #340

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-COC-013..032
**Depends on:** S26 (#315), S06 (#295)

Adding and modifying a conduct outcome, split from S26 (#315).

`steward conduct-outcome add` and `modify`, the form and every field, the 25 limit, and the rule that a conduct outcome holds no time penalty, disqualification, warning point or penalty point — an investigation pertaining to no session.

**What can be exercised:** a league writes the conduct penalties its code of conduct describes.

---

## S51 — Defence submission, merging and withdrawing a report  ·  #341

**Labels:** `module-stewarding`, `feature-request`, `Critical`
**Rules:** STW-CON-032, STW-CYC-044..054
**Depends on:** S12 (#301)

Defence submission, merging and withdrawing a report. Split from S12 (#301).

The defence period that ends for every ticket of the round at once, so a report lodged in the last minute still gets its whole window; two reports of one incident merged by a steward's judgement, the merged one closing as a duplicate; and a complainant withdrawing, which the effective head steward must approve.

**What can be exercised:** the drivers named in a report answer it, and duplicate reports of the same crash become one case.

---

## S52 — Removing, pausing and listing conduct outcomes  ·  #342

**Labels:** `module-stewarding`, `feature-request`, `Medium`
**Rules:** STW-COC-033..056
**Depends on:** S50 (#340)

Removing, pausing and listing conduct outcomes, and the conduct No Further Action. Split from S26 (#315).

`steward conduct-outcome remove`, `toggle` and `list`, what a paused conduct outcome does, and the permanent NFA every investigation ballot offers.

**What can be exercised:** a league prunes its conduct table and reads it back.

---

## What the split turned up

Five things worth your judgement before any of this is filed.

**1. Verdict output has to come before the report cycle finishes.** S30 (#319) (#319) lands in wave 7, two waves
before S13 (#302) (#302) can close a report. It reads oddly — the output before the thing it outputs — but a
report cycle cannot finish without publishing a verdict, and every other kind of verdict (appeals,
CoC, Automated Rulings) reuses the same shaping. Building it once, early, is what keeps S13 (#302) (#302), S15 (#304) (#304),
S19 (#308) (#308), S21 (#310) (#310) and S28 (#317) (#317) from each inventing their own.

**2. The cycle close and the auto-rules depend on each other.** The close checks the auto-rules;
the auto-rules only ever run at a close. I broke it by making S16 (#305) (#305) the close with nothing to check
yet, and S19 (#308) (#308) the rules engine that hooks into it. The alternative is one large issue covering
both.

**3. Two issues have no output a league can see: S10 (#299) (the ticket framework) and S11 (#300)
(deliberation).** They are observable only through S12 (#301) (#301) and S13 (#302) (#302). I kept them separate because
reports, appeals and CoC investigations all three build on them, and merging S10 (#299) into S12 (#301) (#301) would
have the appeal and conduct cycles inherit a framework shaped around reports. If you would rather
every issue stand on its own, merge S10 (#299) into S12 (#301) (#301) and S11 (#300) into S13 (#302) (#302), and accept that those two
become large.

**4. Three issues are big enough to split again**, if the increments turn out unwieldy: S08 (#297) (#297) (66
rules — four near-identical auto-rule forms), S26 (#315) (#315) (63 — the whole conduct outcome table beside the
toggles), S06 (#295) (#295) (55 — the outcome table and its five commands). Each is internally repetitive, which
is why I left them whole.

**5. The two prerequisites that needed issues now have them.**

Both are now filed, on the same milestone: **#279** for the hub channel, which S23 (#312) (#312) cannot be built
without and which #258 and #260 are also waiting on, and **#280** for the constitution, carrying
the licence as the ban state and the appeals lifecycle wording as one amendment — one
`/speckit-constitution` run, one version bump, one sync impact report.

## Housekeeping when these are filed

- Create the `module-stewarding` label.
- Add it to `CONTRIBUTING.md`'s list of module labels, which names five today.
- Put every issue on the **Stewarding module** milestone, created 2026-09-20
  (`milestone/1`), which counts what is left of the set.

## Keeping this current as the module is built

Building will turn up rules that are wrong, rules that are missing, and work that wants an issue of
its own. Nothing here forbids that, and the scheme is built for it:

- **A new rule takes the next free number in its section**, and a rule that goes takes its ID with
  it — retired, never reused. So an ID in a closed issue still names what that issue built.
- **A new issue is added to this document** with the rules it owns, and put on the milestone. Where
  it takes rules from an issue not yet filed, move them; where it takes them from one already
  closed, the new issue says so rather than the old one being rewritten.
- **The coverage is generated, not maintained.** `steward_coverage.py` holds the allocation and
  writes [steward-rule-coverage.md](steward-rule-coverage.md), a row per rule naming the issue
  that owns it. Run it after any change to the spec or to this document: a rule owned by nobody
  shows as `—`, and one owned twice stops the run. That is the check that these three files still
  agree.
- **A rule that changes after its issue is closed needs an issue of its own** — the spec is the
  source, and a closed issue is a record of what was built, not of what is true.
---

## The order the issues fall in

Derived from the dependencies above. Everything in a wave can be built in parallel; a wave
cannot start until the one before it is done.

**Wave 1** — S01 (#290)
**Wave 2** — S02 (#291), S23 (#312), S32 (#321), S39 (#328)
**Wave 3** — S03 (#292), S05 (#294), S06 (#295), S24 (#313)
**Wave 4** — S04 (#293), S07 (#296), S33 (#322), S41 (#330)
**Wave 5** — S08 (#297), S12 (#301), S26 (#315), S34 (#323), S35 (#324), S40 (#329)
**Wave 6** — S09 (#298), S30 (#319), S42 (#331), S44 (#334), S50 (#340), S51 (#341)
**Wave 7** — S13 (#302), S31 (#320), S43 (#332), S45 (#335), S46 (#336), S47 (#337), S52 (#342)
**Wave 8** — S48 (#338)
**Wave 9** — S38 (#327), S49 (#339)
**Wave 10** — S14 (#303), S18 (#307), S28 (#317)
**Wave 11** — S15 (#304), S27 (#316)
**Wave 12** — S16 (#305), S37 (#326)
**Wave 13** — S17 (#306), S19 (#308), S20 (#309), S36 (#325)
**Wave 14** — S21 (#310)
**Wave 15** — S22 (#311), S25 (#314), S29 (#318)

---

## Coverage

Generated from the spec by `steward_coverage.py`; the rule-by-rule view is
[steward-rule-coverage.md](steward-rule-coverage.md).

| Issue | Rules |
|---|---|
| S01 (#290) | STW-CON-001..008, STW-CON-040..041, STW-CON-055..067, STW-CON-073..074 |
| S02 (#291) | STW-CFG-001..002, STW-MOD-001..010, STW-MOD-015..016, STW-MOD-018..023 |
| S03 (#292) | STW-CHN-001..015 |
| S04 (#293) | STW-CON-021..029, STW-TEM-001..018 |
| S05 (#294) | STW-APL-001..006, STW-APL-008..009, STW-JUS-001..003, STW-JUS-008..015, STW-TIM-001..018 |
| S06 (#295) | STW-CON-053..054, STW-OUT-001..026 |
| S07 (#296) | STW-PEN-001..024 |
| S08 (#297) | STW-ARL-001..024, STW-CON-068..070 |
| S09 (#298) | STW-SET-001..012 |
| S12 (#301) | STW-CON-031, STW-CON-039, STW-CON-042..048, STW-CON-050, STW-CYC-022..043, STW-TKT-001, STW-TKT-016..017, STW-TKT-048..051 |
| S13 (#302) | STW-CON-033, STW-CYC-055..058, STW-DEL-001..016, STW-DEL-018..021 |
| S14 (#303) | STW-APL-007, STW-APL-010..011, STW-CON-034, STW-CON-049, STW-CON-051, STW-CYC-065..091 |
| S15 (#304) | STW-CON-035, STW-CYC-092..107 |
| S16 (#305) | STW-CON-030, STW-CYC-108..115 |
| S17 (#306) | STW-CYC-001..021 |
| S18 (#307) | STW-CYC-116..123 |
| S19 (#308) | STW-ART-001..011 |
| S20 (#309) | STW-BAN-001, STW-BAN-007..008, STW-BAN-047..065 |
| S21 (#310) | STW-BAN-002..006, STW-BAN-016..018, STW-BAN-023..035, STW-BAN-040..046 |
| S22 (#311) | STW-BAN-010..015 |
| S23 (#312) | STW-LIC-001..011 |
| S24 (#313) | STW-LIC-012..020 |
| S25 (#314) | STW-BAN-009, STW-BAN-019..022, STW-BAN-036..039, STW-MOD-011..014 |
| S26 (#315) | STW-COC-001..012 |
| S27 (#316) | STW-BKP-001..009, STW-COC-057..063 |
| S28 (#317) | STW-CCY-001..026, STW-CON-036..038, STW-CON-052 |
| S29 (#318) | STW-REV-001..038 |
| S30 (#319) | STW-VER-001..017, STW-VER-019..041 |
| S31 (#320) | STW-VER-018, STW-VER-042..054 |
| S32 (#321) | STW-STD-001..005 |
| S33 (#322) | STW-SHT-001..015 |
| S34 (#323) | STW-SHT-016..038 |
| S35 (#324) | STW-PCK-001..003 |
| S36 (#325) | STW-RST-001..004 |
| S37 (#326) | STW-MOD-017, STW-TST-001..006 |
| S38 (#327) | STW-JUS-004..007 |
| S39 (#328) | STW-CON-009..020, STW-CON-075..076 |
| S40 (#329) | STW-TEM-019..032 |
| S41 (#330) | STW-OUT-027..055 |
| S42 (#331) | STW-ARL-025..046, STW-CON-071..072 |
| S43 (#332) | STW-ARL-047..066 |
| S44 (#334) | STW-TKT-002..013 |
| S45 (#335) | STW-TKT-018..029, STW-TKT-043..047 |
| S46 (#336) | STW-TKT-014..015, STW-TKT-030..036 |
| S47 (#337) | STW-TKT-037..042 |
| S48 (#338) | STW-DEL-017, STW-DEL-022..030 |
| S49 (#339) | STW-CYC-059..064, STW-DEL-031..037 |
| S50 (#340) | STW-COC-013..032 |
| S51 (#341) | STW-CON-032, STW-CYC-044..054 |
| S52 (#342) | STW-COC-033..056 |
