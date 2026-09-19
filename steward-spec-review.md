# Review of `docs/wip-specs/steward_module_specification.md`

Branch `steward_module_specification`, 29 commits, **rebased onto `main` (`26dc02d`, after #242 and #245) on 2026-09-18** — twice, clean both times.
The spec file itself is 769 lines and byte-identical to the pre-rebase version.
Reviewed against the now-current specs, how-to guides, README and `src/`.

Read at the same altitude the document is written at — high-level functional
specification, the attendance spec as the register analogue. Findings about Discord
mechanics are quarantined in §K and are explicitly the lowest priority; everything
above it is about what the bot is being asked to *do*.

Every finding is numbered so we can walk them one at a time. Line numbers are of the
file as it stands on the branch.

---

## §A — Cross-document conflicts with `main`

**Rebased 2026-09-18.** The branch's 29 commits replayed onto `main` with no conflicts —
`main` never touched this file, which is empty there. The spec text is byte-identical to
what I first reviewed, so §B–§K below stand unchanged. What moved is the *context*: all
fourteen other specs, guides and the README are now current in this workspace, and that
changed §A substantially.

**What the rebase settled:** nothing. Every original §A finding survives — none of them
were artefacts of reading a stale tree.

**What it sharpened:** A2, A3 and A5 are worse than I could see before. And it exposed a
whole conflict I had no way to find from the old tree — the verdict graphic (A10–A16),
which `main` has since specified in full, in a shape this document contradicts on nine
counts.

---

**A1. The season lifecycle was reworked (#225) and the document still speaks the old
language.** Line 5 says the module "may not be enabled once the season is approved"; line
10 and lines 81/85 say things are shown in, or fail, "the season review". There is no
season approval and no single "season review": a season moves through ten states, there is
a **configuration review** and a **placements review**, and the governing rule is "Every
other module may be enabled until the season's **placements are first confirmed**". Five
references to restate. The channel validations at 81/85 must also say *which* review
refuses them — division channels are checked at the placements review, and the image spec
shows the house pattern for saying so ("at the configuration review and at the placements
review").

**A2. Drivers now sign up per season — and the signup spec has been scrubbed of bans
entirely.** This undermines lines 56 (tokens expire at season's end), 146 (tokens reset on
team assignment) and 659–660/673–674 (bans carry into the next season, attaching to "the
same division tier"). *Sharpened by the rebase:* `signup_module_specification.md` now
contains **not one reference to a ban** — the word appears once, inside "abandoned". So
lines 681 and 690 ("they will be unable to engage the sign-up wizard") describe a
behaviour the signup spec no longer has any hook for. The stewarding spec has to specify
that hook itself, or signup has to get it back.

**A3. The two driver ban states were deleted (#227), and deleted *pointing at this
module*.** Core, line 406, in terms:

> No state shall bar a driver from signing up. A driver shall not be banned: sanctions
> belong to the stewarding module, which shall bring the bar together with the commands
> that impose and lift it.

This document is that promise's payee and does not honour it. It bars a driver by **role**
(66, 67, 680–682, 689–691) and never says what a banned driver's **state** is, nor which
transitions exist. Combined with A2, there is now *no document anywhere in the repo* that
says how a banned driver is stopped. Either core gets the two states back, or this spec
states plainly that the bar lives outside the state machine and says what enforces it at
each of the three points that matter (the signup button, placement into a team, and the
check-in).

**A4. Core owns round states named after this module's work, and the results spec now
spells out who ends them.** Core gives a round six states, two being **awaiting report
verdicts** and **awaiting appeal verdicts**. The results spec, line 17, says disabling
the results module "shall end every round of that season still awaiting results, report
verdicts or appeal verdicts … Nothing else could ever move such a round once the module is
gone." Once stewarding owns reports and appeals that is no longer true — stewarding could
move them. Which module's disabling ends such a round? Both? Either? And what does *this*
module's disabling do to one? Unaddressed, and the results spec is now actively wrong about
it.

**A5. The round state machine and this document's timeline disagree.** Core moves a round
to *awaiting report verdicts* "once its **results are posted**". Lines 28 and 362 open
report submission "at the scheduled round time" — before the round is raced. Line 367 then
derives the round from "the most recent round that **took place**", which at that instant
is the previous one. See C1; this remains the single biggest timeline problem.

**A6. Core and the README both count the division channels, and the count is about to
change.** Core: "Three channels shall be configured for the server … and **eight** for each
division". README line 269: "the **eight** `/division …-channel` commands". README line
663: "These **eight** channels are one per kind of image output." This document adds a
ticket channel and a licence channel per division, plus a conduct verdicts channel and two
server-scope steward channels. Three places to update, and the last of them ties the count
to the image module's outputs — which matters for A16.

**A7. "A channel does one job" is never invoked.** README line 269 states it for every
channel-setting command: a channel already set as something else anywhere on the server is
refused, naming what holds it. Five new channel commands here say nothing about it. Either
they are bound by it or they are a stated exception. Note the results how-to explicitly
permits results, standings and verdicts to be *the same* channel, so the rule is narrower
than it first reads and the steward channels need their own answer.

**A8. Server-administrator permission is not a route to anything.** Lines 2–3 say module
enable/disable "May only be used by **server admins**". Core: "Both tiers shall be roles
the league configures, and **a permission of the server shall be a route to neither**."
Enabling a module is a league admin's act. Rewrite to the two tiers.

**A9. The permission numbering is invented here and exists nowhere else.** Line 12 gives
"head steward (level 3) and steward (level 4)", line 13 maps managers to "level 2" and
admins to "level 1", line 204 says "league admins (tier 1)". No numbered permission level
exists anywhere in the repo — not a spec, not the README, not `src/`. Core: "**Two** tiers
of authority shall govern every command, and every command shall sit in one of them **and
no other**." And core uses "tier" for a *division's* standing, so "tier 1" collides with
"Tier 1 is the highest". Drop the numbers, name the two tiers, find a different word for
steward authority.

---

### The verdict graphic — specified on `main` since this branch forked

`main` now carries a complete specification of the verdict graphic and the verdict banner
(image spec, lines ~1300–1420). It was not there when this branch started, and the
`## Verdict output` section here contradicts it on nine counts. This is the largest single
block of rework the rebase exposes.

**A10. The graphic is a one-driver graphic, by explicit design, and this document demands
a list.** Image spec: "A verdict graphic represents **one single decision** taken upon the
drivers of one division", and "The graphic holds **no field addressed by an ordinal, and
declares no collection of any kind**. … the two are the only ones" — it is one of three
graphics in the whole module of which that is true. Line 725 here asks for "Involved
drivers - Mandatory - **multi field** in which multiple display names must be mandatorily
supported". A collection is precisely what the graphic declares it does not have. This is
architectural, not cosmetic, and it is the same one-driver assumption that C2 finds in the
vote tally — the two need settling together.

**A11. `verdict_stage` is fixed text with three permitted values, and this module adds
eight.** Image spec: "The stage of a verdict is fixed text: 'Post-Race Penalty' … 'Appeal'
… 'Attendance Sanction'." This document introduces report verdicts, appeal verdicts, CoC
investigation verdicts, auto-rule verdicts, and four ban served/failed notices (655, 658,
670, 672). Every one needs a stage label, and four of them are the verdict formats §D10
already finds have no specification at all.

**A12. Mandatory fields a CoC verdict cannot fill.** `division_name`, `round_number` and
`session_name` are all **Mandatory**. A CoC investigation is "the only one which is not
linked to a round" (line 57) and belongs to no division. *The remedy already exists and
this document should use it rather than reinvent one:* the image spec solves exactly this
for attendance sanctions — "A verdict of an attendance sanction pertains to no session at
all: its session name field shall be emptied, its removable group removed where the
template declares one, and no error shall be reported. The field remains mandatory, the
template being obliged to declare it and the data determining its value to be nothing."
Follow that pattern for all three fields.

**A13. Fields this document asks for that the graphic explicitly refuses.** Image spec:
"The graphic carries **no image of the track**, no country name, no date of the round, no
result of any session, no points, no lifecycle label, and no name of the steward." Line 722
asks for a "Grand prix flag". Lines 718–720 ask for a league logo, a division logo and
division colour support — none of which is among the graphic's fifteen fields.

**A14. Field names are being invented for fields that already exist.** "Outcome" (727) is
`penalty`. "Original complaint" (726) is nearest to `description`. "Season, division, round
number" (717) is three fields, two optional and one mandatory. Quote the existing names.

**A15. The image section must mirror the textual section exactly, and currently does
not.** Image spec: "Nothing is computed for the graphic, nothing is decided for it" and
"The graphic re-presents the values the textual announcement shows and **never derives them
by rules of its own**. A change to how the textual announcement renders any of them is a
change to the graphic by the same stroke." The two lists here differ — the textual list
(705–712) carries involved drivers and omits logos; the image list (715–728) adds a league
logo, a division logo, division colours and a grand prix flag. Under that rule the image
list cannot carry anything the textual one does not.

**A16. The licence sheet needs an image aspect, a toggle, a template command and a default
filename, and asks for none of them.** The image module has eleven aspects, each with an
`images config toggle` value and an `images template …` command. There is no licence sheet.
Line 749 says only that it "will be similar to that of the attendance sheet". Four things
to add, plus a statement of whether a licence sheet is also drawn as text when the toggle is
off — which is the pattern every other aspect follows. Same question for the automated
auto-rule verdict if it is to be drawn differently from an ordinary one.

**A17. Line 701 re-specifies the verdict banner.** "When reports are first posted for a
round, the bot will post text that denotes what rounds the reports pertain to by referring
to the season, division, round, and Grand Prix name." The banner already does exactly that
and has exactly those fields. But its definition needs one amendment for this module: it
stands above "the run of verdict graphics **a single review** produced" — with stewarding,
a "review" becomes a cycle stage, and a round can now produce two batches (reports, then
appeals), so it is not obvious whether that is one banner or two.

**A18. The verdicts channel already has three producers and is about to have more.**
Results spec line 11: "penalty and appeal verdicts shall be posted by the bot. **Automatic
attendance sanctions are announced in the same channel.**" This document adds report,
appeal, CoC, auto-rule and four ban-notice verdicts to that channel and never mentions the
attendance sanctions it will be interleaved with — which matters for line 697's ordering
rule, since an attendance sanction has no report ID to sort by.

---

### Landed on `main` after the first rebase — #242 and #245

Rebased a second time onto `26dc02d`, again clean. #245 reworked how a driver relates to
their Discord accounts (core, signup, and constitution v12.0.0); #242 made results
amendments all-or-nothing. Reading the constitution for #245 also turned up two older
clauses that bind this module directly (A25, A26).

**A19. A licence and a ticket are keyed by "user ID", and a driver now owns many
accounts.** Core, *A driver's accounts*: "A driver shall own every Discord account they
have raced under. One of them is their current account … **Any of them identifies the
driver.**" It names this module's own objects: "Any account of the driver's shall name
them wherever a driver is named: a result submitted or resubmitted, **a penalty, a pardon,
an appeal**, an amendment, a command, a check-in." Line 44 stores "User IDs of the involved
drivers"; the revoke commands (322–349) fail "if there is no such driver with that ID"; the
licence is never said to belong to the profile rather than the account. State that a
licence belongs to the **driver**, that a ticket keeps the account it was written under
(core: "Nothing the league holds of the driver shall be rewritten"), and that every reader
— the licence sheet, the auto-rules, the appeal eligibility check — resolves an account to
its driver.

**A20. An account change silently lifts a ban.** Core: "Making an account current shall
give it the **signed-up, division and team roles** the driver holds, and take them from the
account it replaces." The season-ban and league-ban roles are not in that list, and a role
is the only thing this document bars a driver with. A league manager reassigning a banned
driver's account — or merging them — hands the driver a clean account with no bar. The
steward, head-steward and temp-head roles have the same gap: a steward who changes account
stops being a steward.

**A21. Bans act on one account; the driver has several.** Line 67 bans an account from the
server; a driver's past accounts are untouched and may rejoin. Core: "Only a driver's
current account leaving the server is the driver leaving it; a past account leaving changes
nothing." And "Only a member of the server may be made current" — so a league-banned
driver's own account can never be made current again while the ban stands. Also nothing
stops a banned person signing up on an account that has never been attached to them, but
that is outside what any bot can know and the spec should say so rather than imply the bar
is complete.

**A22. Merging two profiles says nothing about their licences.** Core: on a merge, "the
other's **results, history, seats, attendance and former-driver flag** join it". Not the
licence. Two licences mean two appeal-token balances (capped at the starting value per
line 145 — sum or max?), two sets of points with their own expiry clocks, two sets of
outstanding bans, and two sets of auto-rule flip-flop states (line 642). Needs a rule here,
and core's list needs the licence added once the rule exists. A related question: may a
merge be refused because one of the two is banned?

**A23. An open ticket across an account change.** Core: an account "is read at the moment
of use, so a submission or a review already open when the account changes accepts both",
and "a signup channel held open … shall move to the new current account". A ticket channel
grants read/write to specific accounts (412–414, 486–488); a vote records a steward by
account; the "Remove driver" and "Request remove driver" buttons validate against the
involved-drivers list. Say that ticket-channel permissions, involvement and votes follow the
driver as the held signup channel does — or copy core's other precedent and refuse the
change while the driver is party to an open ticket, as it is refused "while either the
driver or the new account has a signup in progress".

**A24. Conflict of interest must compare drivers, not accounts.** Line 26 excludes "any
involved driver that belongs to the steward team"; line 25 excludes stewards "driving in the
division". A steward whose steward role sits on one account and whose seat sits on another
(see A20) passes both checks.

**A25. The constitution requires this module to bring a *state*, and it brings none.**
Constitution, Principle VIII, *No ban states*: "A ban is a sanction, and sanctions are the
stewarding module's: it **MUST bring the state, the counter and the commands that write
them in one piece**, rather than inherit a state with no command behind it." This document
brings the commands and a licence counter, and bars by role instead of by state. That is
either a gap in the spec or a deliberate design that needs a constitution amendment — which
per CLAUDE.md goes only through `/speckit-constitution`, never by hand. It sharpens A3
considerably: A3 is no longer "core would like a state", it is "governance requires one".

**A26. "Upheld" means the opposite thing in the constitution.** Constitution, appeals
lifecycle: "**Upheld** (correction applied to the result) or **Overturned** (no change)" —
the *appeal* is upheld. Here, "Uphold verdict" (504, 512) means the *original verdict*
stands, i.e. the appeal fails. The same word, opposite outcomes, both about appeals. Pick
wording that cannot be read either way — "Verdict stands" / "Verdict changed" — or align
with the constitution. The same clause's scope item 10 also names "time penalties,
**disqualifications**" as what stewards adjudicate, which backs C5.

**A27. #242 sets a precedent the cycle's postings should follow.** Results spec, for an
amendment: "An amendment shall not be approved unless everything its approval will do can
be done … Before the season's points are overwritten, the bot shall establish that every
division's configured results and standings channels exist and can be posted to … and of
its **verdicts channel** where an autosack or autoreserve threshold is set", and "An
approval shall not be recorded as a success before the reposting it claims has been done."
This module writes to licences and then posts to four channels per division at cycle close
(545–548), and says nothing about a channel that has been deleted or lost its permissions.
The house answer now exists: check first, apply entire or not at all, record success only
after posting. And by analogy with autosack/autoreserve, auto-rules that post automated
verdicts make the verdicts channel a requirement of the amendment check too — which is a
one-line addition to the results spec once this module lands.

---

## §B — Contradictions inside the document

**B1. When does defense submission run?** Three incompatible answers:
- Line 29: "Active **from the moment the report is created**, and automatically disabled
  after a configured amount of time **after the scheduled round time**."
- Line 384: "After valid submission, the ticket's state changes **immediately** to the
  defense submission stage."
- Line 389: "Once the period of time configured for the duration of the **report
  submission stage elapses**, a countdown … **will start**."

The first two are per-ticket and overlapping with report submission; the third is
sequential and per-round. Line 385 ("all tickets' defense submission stage ends at the
same time") is consistent with the first two and not the third. Line 139's 168-hour sum
only makes sense under the third. This has to be settled before anything downstream can
be — it determines whether a defense window is 24 hours or 4 minutes.

**B2. Does the head steward keep their powers while a temp head is active?**
- Line 19: "the head steward loses their default status as effective head steward on all
  tickets **and the ability to use commands which require head steward privilege**."
- Line 107: "The head steward does **not** lose any of their powers with the exception of
  the voting tie-breaking capabilities."

Flat contradiction. Worse, line 19's reading **deadlocks the bot**: `steward
remove-temp-head` is "made available to the head steward" (line 108), so under line 19 the
head steward cannot remove the temp head, and the temp head has no command to resign.
Whatever we settle, someone must be able to end a temporary appointment.

**B3. Are ticket channels kept or deleted?**
- Line 455: "The channel will **not** be deleted even after publishing of the verdicts."
- Line 537: with backup on, channels are persisted "and once successful, the channels
  **will be deleted**".
- Line 538: with backup off, "a 7 day countdown will be initiated, at the end of which
  the channels **will be deleted**".

Under 537/538 they are always deleted; 455 says never. Line 481 also has the appeal
channel link back to the report channel, which needs the report channel to survive.

**B4. Where does a Code of Conduct verdict go?**
- Line 35: "posted to the configured verdict channel **of all divisions the reported
  drivers are assigned to** (… repeating posts must have the indication '(repost)')".
- Line 621: "posted immediately in the channel configured by **'division
  conduct-verdicts-channel'**".

And both contradict line 37, which says a CoC investigation "is **always private to the
utmost** (only the steward team and the driver involved can see this)" — a verdict
posted in a division verdicts channel is the opposite of private. Three rules, three
different audiences.

**B5. Who may open a Code of Conduct investigation?**
- Line 33: "can be initiated by **a member of the steward team** at any time … It kicks
  off when the **head steward (or temporary head steward)** initiates" — the
  contradiction is *inside a single bullet*.
- Line 57: "It may **only** be initiated by the head steward."
- Line 562: the command "will be made available to **the stewarding team**".

**B6. Can the head steward write in a ticket channel, and does that break anonymity?**
Line 353 is a principle: "It is imperative that the steward team is seen as a unified
front, so **no public messages will identify or mention a member of the steward team**."
Then line 413 gives the effective head steward "read/write and attach media permission"
in a channel the involved drivers are reading, line 392 has the bot "post a message
**tagging the effective head steward**" in that same channel, and line 382 posts a
button there to reassign the effective head steward. The principle is broken three times
in the section that follows it.

**B7. May a steward from outside the division lodge an appeal?** Line 150 says the token
cost "shall be ignored if the appeal is initiated by a member of the steward team AND
said member is **not** assigned to a team of the division". Line 466 says the button
verifies the presser "**Is part of the division** of that report". The exemption can
never fire.

**B8. The default NFA outcome does not satisfy the rules for an outcome.** Lines 180 and
184 require "At least one of the … fields must be different from 0", and the NFA outcome
(lines 192–203) is all zeroes by definition. Either NFA is stated as exempt or the rule
is stated as applying to league-defined outcomes only. Same for the conduct NFA
(lines 242–249) against line 230.

**B9. Toggling a penalty type off can produce an outcome that is illegal by the same
rules.** Line 114 zeroes the offending field in every outcome that used it. An outcome
whose only sanction was that type becomes all-zero — violating 180/184, and becoming an
indistinguishable duplicate of NFA. Needs a rule: refuse, or delete the outcome, or
something.

**B10. Outcomes may be added mid-deliberation; outcomes may not be changed
mid-deliberation.** Line 178 makes `outcome add` the one exception "if any report
deliberation or appeal deliberation phases are on-going", while modify (182) and remove
(186) are refused. But adding one mid-vote means stewards who voted early chose from a
different menu than those who voted late, which is a fairness problem in a body whose
whole output is a vote. Worth a deliberate decision rather than an exception granted for
convenience.

**B11. The results module's verdict output is said to be untouched, and is also said to
be unreachable.** Line 7: with stewarding on "it shall not be possible to use the penalty
and appeal functionality in the results & standings module". Line 694: "The current
verdict output is utilized and governed by the results & standings module … not
interfering with the way the results & standings module works at the moment." If the
functionality that produces those verdicts can't run, there is nothing left to not
interfere with. Say plainly that stewarding *replaces* the producer and *reuses* the
renderer.

**B12. Qualifying and race bans: which division are they served in?** Line 64/65 say the
ban applies to "the division in which they received a qualifying ban for". Lines 648 and
664 say it is served "in the **highest tier** division for which they are a full-time
driver". For a driver in two divisions these give different answers.

**B13. A vote's outcome list is not filtered by session.** Outcomes carry "Applicable to
qualifying?" and "Applicable to race?" (lines 169–170) and `outcome list` takes a session
type (line 187) — but the vote modal offers "**all** outcomes currently configured"
(line 425). The applicability flags do nothing at the only moment they matter.

**B14. Majority or plurality?** Lines 58 and 59 define an outcome as "decided from the
**majority** verdict among votes casted"; lines 439–441 and 520–523 tally by
**plurality**. These are different rules and produce different verdicts.

**B15. The tie-break is described two ways.** Line 20 says the effective head steward's
"vote may also serve as a tie-breaker". Lines 442–446 say a tie is broken by the head's
vote *if they voted for one of the tied pairs*, then by a button, then by "the
driver-outcome pair that reached their final vote count the earliest". The concept
section should match the mechanism.

**B16. `steward penalty toggle` cannot toggle two of the penalty types it governs.**
Line 112's list is "warning points, penalty points, time penalties, qualifying bans,
season bans, league bans" — it omits **race bans** and **discipline points**. Yet line
339 has `revoke race-ban` fail "if race bans are **disabled**", and discipline points are
a penalty type on the licence. Either the list is incomplete or those two are
permanently on.

**B17. The conduct-outcome modal has no qualifying-ban field, but three other rules
assume one.** Lines 220–227 list Discipline points, Race bans, Season ban, League ban.
Line 230's validation names "**Qualifying bans**". Line 247's default NFA lists
"Qualifying ban - 0". Line 59's concept includes qualifying bans. The field is missing.

**B18. `steward head-role` says a head steward is mandatory and also optional.** Line 18:
"It is **mandatory** that a stewarding team has a head steward." Line 99: "If the input
role parameter is empty, then head steward functionality is **deactivated**." If it can
be deactivated, the entire "effective head steward" apparatus — tie-breaks, approvals,
justification confirmation, revoke commands — has no holder.

**B19. Reports may be public or private, and nothing ever makes one so.** Line 37 draws
the distinction carefully ("public (seen by any driver of the division) or private (seen
only by drivers involved and the stewarding team)"). No command sets it, no modal field
asks it, no rule keys off it, and the permission rules at 412–414 describe only the
private case. The concept is dangling.

**B20. The steward team may report "as an anonymous collective", and no mechanism
exists.** Lines 38 and 51 both say a report or appeal may be "submitted … by a member
representing the steward team as an anonymous collective". Line 363 only says stewards
may use the ordinary button — which names the submitter and puts them in the involved
drivers list (line 371), destroying the anonymity and making them a party to their own
case.

---

## §C — Workflows that don't hold up

**C1. Reports open before the race is over.** Per A5: line 362 starts the report window at
the round's scheduled start. An F1 league round runs an hour or two; results are entered
afterwards. So the window that should open when there is something to complain about
opens before the lights go out, and burns its 48 hours partly on the race itself. The
natural trigger is the posting of provisional results. This also fixes line 367's "most
recent round that took place".

**C2. Only one driver can be punished per report.** Line 441: "If any one driver-outcome
pair reaches plurality without a tie, the ultimate result of the report will be **that**
outcome being applied to **that** driver." A first-lap pile-up naming three drivers can
only ever end in one sanction. It gets worse under plurality: with nine stewards, three
voting "A — 5s", three "B — 5s" and three "NFA", NFA wins outright and nobody is
sanctioned. The author has flagged the *input* half of this at lines 427, 507 and 595;
the *tallying* half (441, 523, 609) is where the rule actually bites, and it needs the
same flag.

**C3. NFA competes against every driver-outcome pair as a peer.** Because NFA is an
outcome and the winner is a single pair, a report where stewards agree *something*
happened but disagree *who* is at fault resolves to no action. A two-stage tally (guilt,
then sanction) or a per-driver tally would avoid it; either is a real design decision.

**C4. An appeal cannot express the thing appeals are for.** Line 505 offers "all outcomes
currently configured **except for the one dictated in the original report**", and line 512
requires a driver of "None" when upholding. So:
- To *rescind* a penalty, a steward must pick NFA — which forces driver "None" (line
  513), so the verdict never names the driver whose penalty is being lifted.
- To *reduce* a penalty to a lesser amount of the same type, they need an outcome of the
  same type, which may or may not exist, and definitely may not be the original.
- Line 542 nevertheless expects appeals to show as "-5s in the appeal column", i.e. a
  signed credit. Nothing in the outcome model produces one: line 171's time penalty field
  is "Number of milisseconds **added**", and line 180 requires it be non-zero, with no
  statement that negatives are allowed.

**C5. There is no DSQ.** The results module's existing penalty stage accepts "DSQ" as a
sanction and ranks the entry last. The steward outcome model has no equivalent. Turning
stewarding on therefore *removes* a league's ability to disqualify, which no league will
accept.

**C6. Time penalty granularity disagrees with the results module.** Line 171 takes
milliseconds; the results module takes whole seconds and "Fractions of a second shall be
rejected". Pick one, and say what the verdict renders (results spec already pins the
rendering: "+5s", "+5.500s").

**C7. The 168-hour validation does not bound the cycle.** Line 139 sums the five
configured periods. It ignores: the 1-hour tie-break timer (446, 528), the 1-hour
justification-confirmation timer (451, 533) — both of which fire *per ticket* — and the
rule that a phase is over only when **every** ticket in it is posted (456, 539). Twelve
reports with an absent head steward add twelve hours per stage, on top of 144. The bound
should either cover the real worst case or be dropped as decoration.

**C8. One unresponsive head steward blocks a whole round.** Lines 452–454: nothing is
posted until every report's output is settled, and each unsettled one costs an hour of
timer. Combine with line 383 (the bot picks a random effective head steward only if the
*original* head hasn't reassigned by deliberation) and a single absentee stalls the
division. Worth an escalation rule.

**C9. "A random member of the effective steward team is to be chosen by the bot"** (lines
383, 484, 575). Randomness is the one choice a league cannot predict, appeal or test. The
attendance module solves the analogous problem — which reserve gets which seat — with an
explicit ordered priority list. Do the same here.

**C10. The same applies to the tie-break fallback.** Line 446: "the driver-outcome pair
that reached their final vote count the earliest". With votes that can be edited and
removed (435, 437), "reached their final vote count" is not a well-defined moment.

**C11. The Mute/Unmute rules are inverted.** Lines 405 and 407: "The command is rejected
if the user is **not** the one who initiated the ticket, **or** if they are not in the
involved drivers list." Read literally, the only person who can ever be muted is the
reporter. The same bullets end "This can be used on stewards as well" — and stewards are
never in the involved drivers list. Both clauses need rewriting; I suspect the intent was
"rejected if the target is neither the initiator nor an involved driver nor a steward".

**C12. "Request input from driver" is an add-driver button wearing the wrong name and the
wrong text.** Line 391 calls the justification "(optional)" in its opening sentence and
"(mandatory)" in both sub-bullets. Both sub-bullets describe "the one requesting this
**exclusion**" when the button adds a driver. And it sits beside "Remove driver" and
"Request remove driver", so the natural names are "Add driver" and "Request add driver".

**C13. Drivers can ask for a driver to be removed but not added.** "Request remove driver"
(398) is open to "members of the effective steward team … and drivers"; the add path (391)
is stewards only. A driver who knows a fourth car was involved has no route.

**C14. A head steward who wants out can be refused and then has no exit.** Line 402: the
head nominates a replacement, "the member will be requested to accept or reject", and on
rejection the process simply ends with a notification. There is no second attempt, no
fallback to the random pick of line 383, and no statement that the head remains head.

**C15. Line 401's rejection notice mentions the wrong person.** "If rejected, then a modal
will be opened to assign a justification (optional), and after confirmation, the
**effective head steward** will be mentioned in a message in the steward command channel
informing of the decision." The head steward is the one who did the rejecting; it is the
*requesting steward* who needs telling.

**C16. Exclusion is available during deliberation; adding and removing drivers is not —
and nothing says so.** Lines 395/397/399 reject add/remove "if the ticket is already in
report deliberation or appeal deliberation", while line 403 explicitly contemplates an
exclusion requested by someone who has "already cast their vote in the deliberation
phase". But the buttons are only ever specified as appearing in the *defense submission*
phase (390). State which buttons survive into deliberation.

**C17. An excluded steward's vote is discarded, changing the arithmetic after the fact.**
Line 403. If a steward withdraws at hour 23 of a 24-hour deliberation, the plurality can
flip with no chance to re-vote. Worth an explicit rule about whether the tally is
recomputed and whether the deliberation extends.

**C18. Stewards have read-only access during defense submission in the stewarding cycle
and read/write in the conduct cycle.** Lines 412 vs 581. Probably deliberate (drivers are
present in one and users in the other), but the difference is unexplained and reads like
an oversight.

**C19. The qualifying-ban served/failed tests leave a hole and contradict the ban's own
definition.**
- Line 64 forbids "taking part in **all** qualifying sessions"; line 654 tests only "the
  **feature** qualifying session's results". A sprint-format round has two.
- Line 654 additionally requires the driver be "present in the results of at least one
  **other** session". A driver who properly sits out qualifying and then DNFs out of the
  results of the race is recorded as not having served.
- Line 656 covers a driver missing from *all* sessions; line 657 covers a driver who *set
  a valid lap*. A driver absent from qualifying but present in the race falls between
  them.
- Line 654 wants "fastest lap = N/A". The results spec says a qualifying table's best-lap
  column is *emptied* for a classified entry that set no time, and carries DNF/DNS/DSQ
  otherwise. "N/A" is not one of the values it describes.

**C20. Race bans have no reserve fallback.** Line 648 gives qualifying bans one ("If the
driver is not a full-time driver for any division, then … the highest tier division for
which they are a reserve driver"). Line 664 has no such sentence, so a reserve-only
driver's race ban has no division to be served in.

**C21. A qualifying-banned driver is told at the worst possible moment.** Lines 650 and
652: notified "**at the scheduled time for the round**" — after check-in has closed, after
reserves have been distributed, as the session starts. Compare the race-ban path (665),
which acts on check-in. The notification should land with the check-in call.

**C22. A season ban vacates every seat mid-season and nothing catches the fallout.** Line
678. What happens to the points they have already scored, to their team's constructor
total, to a round whose reserves were distributed on the assumption they were racing, to
an open ticket they are a party to? Core's `sack` path exists; say whether this reuses it.

**C23. A league ban bans the user from the Discord server and then assigns them a role.**
Line 67: "banned from the league server for a configured duration … **Upon rejoining the
server**, all users that receive a league ban will be assigned a special role". Meanwhile
`revoke league-ban` (349) says "the league ban role will be removed from them" — from
someone who is not in the server. And no command anywhere configures that duration
(see E4).

**C24. "Number of races" as a season-ban length is not defined in time.** Line 117: "a
hard number of races equivalent to the total number of rounds of the season in which the
ban was acquired". Counted from when — the ban, or the next round? Counted in which
division, for a multi-division driver? Counted across a season boundary? And "races"
versus "rounds" are used interchangeably throughout (a sprint round holds two races).

**C25. "A week after the final scheduled round" is an arbitrary constant.** Line 118. It
is also not the season's *completion*, which core makes a league admin's deliberate act;
a ban that lifts while the season is still formally open is a different rule from one that
lifts when the season ends.

**C26. Auto-rules can ping-pong forever.** Line 641 proposes re-checking every rule until
a pass triggers nothing. Two rules — "1 warning point → 1 penalty point" and "1 penalty
point → 1 warning point" — never converge. There is no iteration cap, no cycle detection
and no refusal at configuration time.

**C27. The flip-flop rule is defined only for accumulation and applied to everything.**
Line 642 describes a threshold that re-arms when the tally drops back under it. That is
coherent for "active accumulation"; it is meaningless for "single round" (which counts
one round's penalties) and for "historical accumulation" (whose tally never decreases, so
the rule can fire exactly once, ever, per driver). Say which rule types it governs. The
author has already flagged the design at line 643.

**C28. `add-history-acc-round` files rules under the wrong type.** Line 310: "the auto-rule
will be added to the list with the '**active accumulation**' type". Copy-paste from line
297.

**C29. Rules are "made active immediately" and check history.** Lines 269, 284, 297, 310.
A historical-accumulation rule added in round 8 fires retroactively against everything
already on every licence at the next cycle close. Intended? Say so either way.

**C30. An auto-rule triggered by accumulation has no obvious channel or division.** Line
636 posts the automated verdict "in the same channel the verdict which triggered the
auto-rule was". But line 545 checks the rules against **licences** at cycle close, not
against a particular verdict — and for a driver in two divisions, or a rule tripped by a
CoC investigation for a non-driver, there is no such channel. Line 639's ID format
`S<x>_D<y>_R<z>_AR<w>` has the same problem: `D<y>` is undefined.

**C31. The trigger and the sanction may not be the same penalty type.** Lines 262, 266,
277, 281, 290, 294, 303, 307. That forbids the commonest escalation a league writes:
"three penalty points in a season → one more penalty point", or the classic "12 penalty
points → race ban" is fine but "3 warnings → 1 warning" is not. More to the point, the
set of *valid* trigger types is never enumerated — is a time penalty a trigger? a season
ban? — and the sanction set likewise.

**C32. `steward appeal starting-tokens` default 0 is described as unlimited and also as a
maximum.** Lines 144–145: default 0 "means that, effectively drivers have unlimited appeal
abilities", and "This value also serves as the **maximum** allowed number of appeal tokens".
A maximum of zero with a cost of zero happens to work, but the two sentences are saying
opposite things and the interaction should be stated once, plainly.

**C33. Tokens are never actually spent.** Line 467 checks the driver *has* enough; nothing
says they are deducted on submission. Line 56 says "depending on configuration, drivers
may be returned their spent tokens" — a configuration that does not exist (see E5).

**C34. Anyone in the division can appeal anyone's report.** Line 466's only test is "Is
part of the division of that report". A driver with no involvement in an incident can
appeal its verdict. Nothing checks they were an involved driver, nothing checks they were
the one sanctioned, nothing prevents the same report being appealed twice, and line 480's
ID (`<Report ID>-APPEAL`) makes a second appeal a primary-key collision.

**C35. No rule caps reports.** Nothing stops one driver lodging twenty reports in a round,
reporting themselves, or lodging the same incident twice. Nothing lets a reporter withdraw
one.

**C36. "Cycle close" is undefined when there are no tickets.** Line 545 begins "Once all
tickets for a given round of a given division reach this stage". Line 386 says with no
reports "the stewarding cycle is considered closed" — so with zero tickets, do auto-rules
run and does the licence sheet (547) get reposted? A driver can acquire a qualifying ban
from an auto-rule with no report in the round at all, so the answer matters.

**C37. `steward republish-verdict` is unusable for the thing it is for.** Line 552: "It
shall not be possible to execute this command on a report if the stewarding cycle of the
round to which it pertains has **ended**." The typical correction — a typo spotted after
the verdicts went out — happens after the cycle ends. As written the window is only the
appeal phase. Line 553's appeal condition ("the stewarding cycle of the round **after** the
one to which the appeal pertains has moved past the report deliberation phase") is also
undefined for the final round of a season.

**C38. Attendance's pardon mechanism lives in a wizard this module disables.** Line 667
says a race-banned driver "will not be punished with attendance points … and will be
bestowed with an **automatic justification**". The attendance spec calls this a **pardon**,
and its only surface is "a new button … in the penalty wizard" — which is exactly the
results-module functionality line 7 switches off. With stewarding on, there is no pardon
button at all. This needs an answer for pardons generally, not just for the automatic one.

**C39. Attendance points are timed off a results-module event that may no longer fire.**
The attendance spec: "Attendance points shall only be distributed once the **post-race
penalties results are finalized**". With stewarding owning penalties, name the new moment
— presumably the report deliberation close (line 459) or cycle close.

**C40. Nothing describes what happens when the world changes under an open ticket.** No
rule covers: the module being disabled mid-cycle; the results module being disabled
mid-cycle (line 6 promises "only part of its functionality is to be disabled" and never
says which part); a division or season being cancelled with channels open; a driver being
sacked, leaving the server, or re-keyed to a new account (a rule `main` added in #222)
while a party to a ticket; a round being amended.

**C41. Nothing describes restart recovery.** Core enumerates what survives a restart and
names "penalty and appeal reviews, which shall be posted again rather than discarded".
This module has at least seven kinds of timer — report, defense, report deliberation,
appeal submission, appeal deliberation, two one-hour timers, a seven-day channel deletion
— and says nothing about any of them surviving a restart.

**C42. There is no Test mode section.** Line 11 says only "This module must work with the
fake driver rosters used in test mode". The attendance spec carries a full `## Test mode`
section. Cycles measured in days are exactly what a maintainer cannot exercise by hand, so
this is the module that most needs one.

---

## §D — Gaps in the rules

**D1. No way to read a driver's licence.** The licence is the central object of the module
(line 16) and the only view of it is a per-division sheet listing points and outstanding
bans (§ License sheet output). There is no command for a steward, a manager or a driver to
read one licence — its history, its dates of incidence, its expiries, its appeal tokens.

**D2. A season- or league-banned driver's ban is invisible.** Line 746: bans do not appear
on the licence sheet "as once they are handed out, drivers are automatically unassigned
from their seats", and line 734 limits the sheet to drivers assigned to the division. So
the two heaviest sanctions the module can impose are recorded nowhere a league can see.

**D3. No command lists the stewarding team.** No way to see who holds steward status, who
the head steward is, whether a temp head is active, or who the effective steward team for
a ticket is. Every other role-bearing concept in the repo has a view.

**D4. No way to add or remove an individual steward.** Steward status is conferred purely
by holding the role (line 17). Then line 94 says `steward team-role` "is only valid if no
user has steward status" — so once one person holds the role the role can never be changed,
and there is no command to strip it. The same trap applies to `steward head-role` (line 98).

**D5. Nothing prevents the steward role being the interaction or league admin role.** Lines
95, 97 and 101 validate the three steward roles against each other only.

**D6. Warning, penalty and discipline point expiry is specified but not configurable.**
Lines 61–63 say each "may expire either after a set number of races …, upon the current
season's end, or after a fixed period of time". No command chooses which, and no command
sets the number or the period. Compare `steward penalty season-ban-type`, which does
exactly this job for season bans — three commands of that shape are missing.

**D7. "If warning points are enabled, their accumulation will equal a penalty point"**
(line 61) is a rule with no number in it. How many warnings make a point? It also
duplicates what an auto-rule does, so it may want deleting in favour of a shipped default
auto-rule.

**D8. Penalty-point carry-over is assumed by the licence sheet and specified nowhere.**
Lines 764–765 have columns for "Total active penalty points **from prior season** —
Mandatory if penalty points enabled **and penalty points may carry over**". Nothing says
what makes them carry over or what command decides it.

**D9. The licence records no appeal tokens.** Line 16's list of what a licence holds omits
them; line 56 says "Appeal tokens are accumulated on a driver's license".

**D10. Four forward references have no destination.** "The format of this communication
will be specified in a different section" at lines 655, 658, 670 and 672 (qualifying/race
ban served and failed-to-serve verdicts), "The structure of this automated verdict document
will be outlined in a later section" at 637, and "License sheet posting will be specified
in another section" at 548/633 — the last does resolve, the others do not. Five verdict
formats are promised; two are delivered.

**D11. The verdict output omits the infringement.** Lines 705–712 (textual) and 716–728
(image) list no infringement/rule-number field, though both vote modals collect one
"Useful for final verdict write-up" (428, 508) and auto-rules carry one (260).

**D12. The verdict output assumes every verdict belongs to a round.** Lines 707–708 make
"Season, division, round number" and "Grand prix name, session name, lap" part of every
verdict, and line 723 makes "Session name" **mandatory** in the image. A CoC investigation
has none of them (line 57: "the only one which is not linked to a round").

**D13. Publishing the original complaint verbatim.** Line 710 makes "Original complaint" a
mandatory field of a *published* verdict. For a private report or a conduct investigation
that republishes an accusation in the driver's own words to the whole division. Worth a
deliberate decision.

**D14. The licence sheet and the verdict need image-module support that is not asked for.** Superseded by **A10–A17**, which state it against the verdict graphic spec `main`
now carries. Kept here as a pointer so the §D walk-through does not lose it.

**D15. Line 701 duplicates the verdict banner.** Superseded by **A17**.

**D16. The licence sheet's ordering has no direction and no tiebreak.** Lines 735–739 list
four criteria without saying ascending or descending (the attendance spec says "descending
order from most attendance points to least"), and line 736 says "Number of penalty points"
where line 743 says "**active** penalty points" — which is it. Line 745 orders outstanding
bans "race bans first, then qualifying bans" with no tiebreak within either.

**D17. A ban shows on a sheet where it will not be served.** A multi-division driver's ban
is served in their highest-tier division (648) but the sheet lists every driver of every
division they are in (734).

**D18. Nothing says what the reposted results are labelled.** Line 459 reposts results and
standings after report deliberation "with all accured time penalties factored in
exclusively"; line 540 does it again after appeals. The results module names three states
— Provisional, Post-Race Penalty, Final. Say which label each repost carries, and what
marks the round final for core's round state machine.

**D19. `steward command-channel` and `steward log-channel` are not required by any
validation.** Contrast the ticket and licence channels, which fail the season review if
missing (81, 85). Without a command channel, half the module's commands have nowhere to run.

**D20. The backup directory is "by default" and has no command.** Lines 206 and 252. Also
no retention rule, no size bound, and this bot runs on a Pi whose `/tmp` filling is already
a known hazard — downloading every attached video into `./tickets` indefinitely deserves a
sentence.

**D21. The two backup toggles are league admins' and every other command here is not.**
Lines 204, 250. They are also the only commands in the document that write to the host's
filesystem. Worth stating the reasoning the attendance spec gives for its autosack tier
choice.

**D22. The LLM justification mode has no configuration.** Line 156 offers it and says it
"cannot be chosen if there is no valid LLM token/communication set up" — no command sets
one up, nothing says where the text goes, what it costs, or that ticket contents leave the
server. That last point is a league-visible consequence and belongs in the spec.

**D23. The fallback-justification rule at line 164 is dead.** "If neither … are feasible
options, then the bot will provide either 'longest', if possible, or no text at all."
Justification is mandatory on every vote (429, 509, 597), so "longest" is feasible whenever
any vote exists, and where no vote exists there is no outcome to justify.

---

## §E — Commands that are implied but never specified

**E1.** Warning-point expiry type and value (per D6).
**E2.** Penalty-point expiry type and value.
**E3.** Discipline-point expiry type and value.
**E4.** League-ban duration — "banned from the league server for a **configured** duration"
(line 67).
**E5.** Appeal-token refund on a successful appeal — "**depending on configuration**"
(line 56).
**E6.** Warning-points-per-penalty-point ratio (line 61).
**E7.** Penalty-point carry-over between seasons (lines 764–765).
**E8.** Backup directory (lines 206, 252).
**E9.** LLM endpoint/token (line 156).
**E10.** A view of a driver's licence (D1).
**E11.** A view of the stewarding team (D3).
**E12.** Add/remove a steward (D4).
**E13.** Enable/disable an individual auto-rule without deleting it — `auto-rule` has add,
modify, remove and list, but no toggle, where outcomes and penalty types both have one.
**E14.** A view of which auto-rule thresholds a driver currently sits above (the flip-flop
state of line 642 is invisible to everyone, including the driver it governs).

---

## §F — Naming and terminology

**F1. The channel has three names.** "ticket channel" (80, 361, 463), "report channel"
(355, 366), "reports channel" (362, 464). One name.

**F2. "steward team" / "stewarding team" / "steward's team" / "steward's report"** are all
used. Pick one. Likewise "effective steward team" (22, 47, 382) and "effective stewarding
team" (22, 400, 412) appear in the same sentence at line 22.

**F3. "conduct outcome" / "discipline outcome" / "conduct-outcome".** Line 593 says "all
**discipline** outcomes currently configured"; everywhere else it is a conduct outcome.

**F4. "Code of Conduct investigation" / "CoC investigation" / "investigation" / "report".**
Lines 572 and 573 call an investigation "the report".

**F5. "driver license" / "driver's license" / "driving licenses"** (545, 628) / "license".
And per I1, all of them should be **licence**.

**F6. "Uphold verdict" (504, 512) versus "uphold penalty" (521).**

**F7. "reportee" is used for the reporter.** Lines 381, 573. A reportee is the person
reported against. "Reclaimant" (374) is not an English word — "complainant".

**F8. "incidence" for "incident".** Lines 16, 37, 38.

**F9. "Revokement" (heading, line 320).** "Revocation".

**F10. Five toggles, four naming shapes.** `steward penalty toggle`, `steward appeal
toggle`, `steward conduct toggle`, `steward toggle-conflict`, `steward backup
toggle-report`/`toggle-conduct`. Settle on one — `steward <thing> toggle` reads best and
three of the five already use it.

**F11. Conduct commands use three different prefixes.** `steward conduct toggle`, `steward
conduct-outcome add`, `steward conduct-inv start`, `steward conduct
defense-submission-period`. Two of those put "conduct" in a subgroup and two hyphenate it
into the leaf.

**F12. Period commands are inconsistent with their own family.** `steward
report-submission-period` and `steward appeal-submission-period` are hyphenated leaves,
but `steward appeal toggle`, `steward appeal starting-tokens` and `steward appeal
token-spend` treat "appeal" as a subgroup. So "appeal" is both.

**F13. `division conduct-verdicts-channel` is not per-division.** Line 87 gives it only "a
channel" as input, unlike its two siblings which take a division name. Either it takes a
division (and then B4 must be resolved in favour of line 35) or it is named `steward
conduct-verdicts-channel`.

**F14. The two "acc-round" auto-rule commands are not round-based.** `add-active-acc-round`
and `add-history-acc-round` check a licence, not rounds. Drop the `-round`.

**F15. The auto-rule *types* have four names each.** The concept list (68–72) says "Single
round / Multi round / Active accumulation / Historical accumulation"; the commands say
"single-round / multi-round / active-acc-round / history-acc-round"; the confirmations say
"'single-round' type / 'multi-round' type / 'active accumulation' type"; and line 298 calls
one "an accumulation of active penalties rule".

**F16. The module's own key is never named.** Line 2 says it is enabled "via a 'module
enable' command akin to the weather and signup modules" — but never says whether the key is
`stewarding`, `steward` or `stewards`. The file is `steward_module_specification.md` and
the title is "Stewarding module". The README will need the exact token.

**F17. `<COMMAND CHANGE>` versus `<NEW COMMAND>`.** Lines 2–3 mark the module enable/disable
as `<COMMAND CHANGE>`, consistent with the other specs' 9 uses — good. Worth confirming
they are the only two.

**F18. "highest tier division" is ambiguous every time it appears.** Core says "Tier 1 is
the highest", so the highest tier is the *lowest number*. Lines 648, 649, 660, 664, 674 all
say "highest tier"; line 660 then says a ban is "**upgraded** to the highest division",
which suggests movement in the other direction. Say "tier 1 or the nearest to it" once, in
Concepts, and refer to it.

---

## §G — The data model and the IDs

**G1. The report status enum is copied from the appeal.** Line 41: "**INIT - Appeal** just
opened" inside the Report definition.

**G2. The status enum has three values for five stages.** INIT / Delib / CLOSE (40–43,
52–55) cannot distinguish report submission from defense submission, nor mark a ticket
awaiting its justification confirmation, nor mark one whose verdict is posted but whose
appeal window is open. Line 178 then refers to status "**closed**" in lower case, and the
three values are variously upper, title and truncated.

**G3. The ticket object cannot hold a vote.** Line 49 records, per steward, "the ID of the
outcome object … (empty if no vote was cast)". A vote also carries the **driver**, the
**infringement** and the **justification** (424–429) — and for an appeal, the **decision**
(504). None has a home.

**G4. The ticket object cannot hold the outcome list, and line 178 requires it to.** "the
outcome object must be appended to all ticket data objects that are not in status 'closed'"
— there is no such field in lines 38–50, and line 58 says the outcome list "is managed
separately".

**G5. The ticket object holds no complaint, evidence, channel ID or timestamps.** Every one
of them is needed to rebuild the ticket after a restart (C41).

**G6. The session enum omits the non-sprint options.** Line 45 lists "Sprint Qualifying,
Sprint Race, Feature Qualifying, Feature Race"; line 372 says a non-sprint round offers
"Qualifying" and "Race".

**G7. Line 373 keys the lap field off the sprint names only.** "Mandatory if session =
'Sprint race' or 'Feature race'" — so a non-sprint "Race" gets its lap field greyed out.
Casing also differs from line 372's "Sprint Race".

**G8. "Lap (empty if round was a Qualifying round)".** Line 46 — qualifying is a session,
not a round.

**G9. The report ID's zero-padding is undeclared.** Line 39/380 say `<w>` is "the number of
the report"; line 698's example is "S1_D1_R1_001". Without a declared width, the
alphabetical ordering line 697 depends on breaks at ten reports ("…_10" sorts before
"…_2").

**G10. The appeal ID changes separator mid-string.** `S1_D1_R1_001-APPEAL` — underscores
throughout, then a hyphen (line 480).

**G11. One appeal per report, by construction.** Per C34.

**G12. Line 536's ordering claim is wrong in an interesting way.** Appeals are posted "in
appeal ID alphabetical order (which will coincide with the **submission order of the
original reports**)" — true, and it means an appeal lodged first may be posted last. Line
454's equivalent claim for reports ("will coincide with the submission order") is the one
that holds. Worth making the difference explicit rather than parenthetical.

**G13. `COC_INV<x>`'s padding is described wrongly.** Line 572: "with at least **two zeros
to the left** (e.g. COC_INV001, COC_INV067, COC_INV203)". The examples are padded to three
digits; "at least two zeros" would make COC_INV067 wrong and COC_INV203 impossible.

**G14. The CoC auto-rule ID's example is of the wrong thing.** Line 640 gives the format
`COC_INV<x>_AR<y>` and then illustrates it with "COC_INV001, COC_INV067, COC_INV203" — the
investigation IDs, not the auto-rule IDs.

**G15. IDs bake in the division tier, which is amendable.** `D<y>` is "the tier of the
division". Core allows `division amend` to change a tier during setup, and a cancelled
division is excluded from the tier sequence. Say that the tier is frozen at the moment the
ID is minted.

**G16. Three different ID length limits, for no stated reason.** Outcome ID: 10 characters
(166). Conduct outcome ID: 12 (221). Auto-rule ID: 12 (259, 272, 287, 300).

**G17. "an identifying shorthand" is defined and never used.** Lines 58 and 59 say each
outcome has one; the modal has ID, Brief and Description and no shorthand.

**G18. Line 221 is truncated.** "Unique ID for the conduct outcome. **ap with that of other
outcomes.** Maximum of 12 characters." — presumably "…may overlap with…", matching line 59.

**G19. The default NFA outcomes are missing a field each.** The report NFA (192–203) lists
through "Season ban - 0" and omits **League ban**. The conduct NFA (242–249) omits **League
ban** too, and lists a Qualifying ban the modal cannot set (B17).

---

## §H — Structure and register

**H1. `### Revokement` is filed under `## Configuring the stewarding module`.** Revoking a
penalty is an operational act by the head steward, not configuration by a league manager.
It belongs beside the cycles, or in its own top-level section.

**H2. `### Stewarding cycle setup` has become a catch-all.** It holds the five period
commands (which fit), then appeals, conflict-of-interest, justification modes, the whole
outcome table and the report backup. Four of those are not cycle timing. Suggest splitting
into `### Timings`, `### Appeals`, `### Outcomes` and `### Backups`, with the conduct
equivalents folded into the same sections rather than duplicated under `### Conduct cycle
setup`.

**H3. The conduct cycle duplicates the stewarding cycle nearly verbatim.** Lines 589–624
repeat 500–535 with "driver" swapped for "user", and lines 626–633 repeat 621–622. The
attendance spec's approach — state the rule once and name the difference — would cut this
substantially and would stop the two drifting (they already have: B4, C18, and the missing
"verdicts are delayed" notice at 612).

**H4. Appeal submission repeats defense submission's permission and driver-management
rules.** Lines 486–492 restate 408–414. Line 485 already says "the same buttons … with the
same permissions and logic", so the restatement is redundant and is a second copy to keep
in step.

**H5. Implementation guidance in a functional spec.** Lines 460 and 541 ("This functionality
is somewhat implemented already, just a matter of reusing it"), 641 ("the easiest way to
implement this is…"), 551 ("If this is not technically feasible due to Discord API
limitations…"), 695 ("The latter's implementation must be used as much as possible, down to
the templates"), and the modal mechanics throughout. The repo's rule is that engineering
decisions live in docstrings, not wip-specs.

**H6. Placeholder text left in.** "etc etc etc" at lines 352 and 556.

**H7. There is no `## Test mode` section** (C42).

**H8. There is no statement of what the module does when its dependencies are off.** Line 6
("only part of its functionality is to be disabled if the results & standings module is
disabled") names no part. Core requires each module's spec to carry its own enable, disable
and dependency rules, and to state the cascade.

**H9. Nothing states when the configuration commands may be run.** Core: settings may change
freely "until placements are confirmed; once placements are confirmed, **on the terms its
own specification sets** for a season being raced". Some commands here carry a condition
(no deliberation in progress); most carry none.

---

## §I — Language, spelling, British English

The repo rule is British English throughout, prose and identifiers alike. This file is the
**only** file in the repo that spells it "license"; `docs/`, the README and `src/` all use
"licence".

**I1. licence.** "driver licence", "`division licence-channel`", "licence sheet" — every
occurrence, including the command name and the section heading — 69 in all.
**I2. behaviour.** "behavior" (116, 391, 400), "behaviors" (116).
**I3. colours.** "Division colors support" (720, 756).
**I4. -ise.** "standardized" (58, 59), "summarized" (381, 573), "penalized" (427, 507),
"penalize" (595), "utilized" (694), "organized".
**I5. offence.** "offense" (195, 245).
**I6. milliseconds.** "milisseconds" (171) — also in the results spec, so worth a sweep.
**I7. responsibilities.** "responsabilities" (18, 19).
**I8. against.** "againt" (64, 65).
**I9. accrued.** "accured" (69, 70, 459, 285-ish).
**I10. conduct.** "`steward backup toggle-conmduct`" (206).
**I11. "theeffective"** (401) — missing space.
**I12. "throughout their in the league"** (311) — missing word.
**I13. "to server"** (745) — "to serve".
**I14. "there is not actionable offense"** (195, 245) — "no actionable offence".
**I15. "A individual record"** (16) — "An".
**I16. "an driver-outcome pair"** (446, 528), "an user-outcome pair" (613), "a appeal"
(529), "an multi-round rule" (314), "an conduct outcome's ID" (231, 235).
**I17. "It is possible that the triggering an auto-rule of one kind triggers an auto-rule
of one kind."** (313) — garbled; presumably "of **another** kind", which is what the
sub-bullet at 314 then says.
**I18. "This command shall enable that penalty type, if configured, and disable it, if not
configured."** (112) — circular. It toggles.
**I19. "the content of the channels of all channels"** (537, 623).
**I20. "persisted onto memory"** (537, 623) versus "persisted in disk" (204, 250) — both
should be "to disk", and 537/623 mean disk, not memory.
**I21. "Json"** (207, 253) — JSON.
**I22. "his"** (155) — "their".
**I23. "Contrary to the others"** (178, 228) — "Unlike the others".
**I24. "Appeal submission - Active for a configured period of time after the report
deliberation ends. Lasts for a configured period of time"** (31) — the second sentence
repeats the first.
**I25. "Report verdicts for round <x> are slightly delayed, please stand by."** (444, 526)
— informal for bot output, and it names no division, which matters because verdicts are
per-division.
**I26. "Driver-outcome pairs are both considered a vote"** (440, 522, 608) — "both" has no
referent; presumably "the driver and the outcome together are considered one vote".
**I27. "Defense"** (21 occurrences, including the headings at 388 and 578) — American. British English is
**defence**. Note the wip-spec would then read `steward defence-submission-period`.
**I28. Trailing whitespace** at line 507, and a line of trailing spaces at 461.

---

## §J — The author's own open markers (inventory, so none get lost)

**J1.** Line 368 — `<CHECK FEASIBILITY OF USING A CHECKLIST WITH ALL DIVISION DRIVERS>`.
**J2.** Line 427 — `PROBLEM WITH THIS DESIGN`: one driver per vote, report deliberation.
**J3.** Line 507 — same problem, appeal deliberation.
**J4.** Line 595 — same problem, investigation deliberation.
**J5.** Line 643 — `<TBD> - DISCUSS/WEAK POINT` on the flip-flop threshold.
**J6.** Lines 460, 541 — "somewhat implemented already".
**J7.** Line 551 — "If this is not technically feasible due to Discord API limitations".
**J8.** Line 641 — "the easiest way to implement this is…".

To these I would add C2/C3 as the *unflagged* half of J2–J4: the input problem is marked,
the tallying problem is not, and the tallying problem is the one that decides whether a
multi-driver incident can be judged at all.

---

## §K — Implementation-shaped notes (lowest priority, and deliberately last)

Recorded only because a few of them may change what the spec should *say* it does, not
because the spec should describe mechanics.

**K1.** Discord modals hold at most five text inputs and nothing else — no dropdowns, no
checkboxes, no file uploads, no user mentions, no greyed-out or read-only fields. The
report modal asks for nine fields including a dropdown, a file upload and mentions (364–377);
`outcome add` asks for thirteen including four checkboxes (165–177); the vote modals ask for
six including two dropdowns (421–430); `republish-verdict` asks for five greyed-out fields
plus one editable (549). Every one of these will land as a multi-step flow of views and
modals, as the existing penalty wizard does. If the spec means to stay silent on mechanics
it should describe these as *what is collected*, not as *a modal with these fields* — which
is also what the attendance spec does.

**K2.** The 0..5 evidence files at lines 375, 475 and 566 cannot be collected in a modal at
all; the existing bot collects attachments as messages in a channel.

**K3.** Roughly 53 steward commands are specified. A Discord command group holds 25
subcommands, so `steward` will have to split — probably along the subgroup lines F10–F12
are already reaching for.

**K4.** `steward outcome list` and friends return "a plain text table" (317) of eight
columns; Discord's 2000-character message limit will bite on a league with many rules.

**K5.** Line 551's fallback — delete and repost every verdict message — is the right
instinct (Discord will not let a bot edit attachments freely), but as written it reposts
*all* verdicts to fix one, which changes their timestamps and pings. Worth deciding the
league-visible behaviour rather than leaving it as an API caveat.

---

## Suggested order of attack

*Revised after the rebase.*

1. **§A1–A9 and A19–A27** — the lifecycle, permission and account language. Mechanical, and everything else is
   written on top of it. A3 and A25 are the ones with a deadline attached — the constitution now *requires* this module to bring a ban state: `main` deleted the ban
   states *on the strength of this document*, and until it delivers, no document in the
   repo says how a banned driver is stopped.
2. **A10–A18 together with C2** — the verdict graphic. The image spec's "one single
   decision … no collection of any kind" and the tally rule's "one driver-outcome pair
   wins" are the *same* assumption reached from two directions. Decide once whether a
   verdict is per-driver or per-incident, and both fall out.
3. **B1** (when defense submission runs) and **C1** (when reports open) — everything
   downstream is timed off them.
4. **C3, C4, C5** — the rest of the voting and outcome model: NFA competing as a peer, an
   appeal that cannot rescind, and the missing DSQ.
5. **B2, B4, B5, B18** — the head steward and the conduct cycle, where the contradictions
   are flat and small.
6. **§D/§E** — the missing rules and commands, mostly mechanical once the above are settled.
7. **§F/§G/§I** — naming, IDs and language, best done last and in one pass.

---

## Owed to other documents — deferred to implementation (decided 2026-09-18)

Recorded as the walk-through decides them. None is to be made until stewarding is built.

- **Constitution, Principle VIII** — the driver licence is the "state" the module brings (A3/A25). Via `/speckit-constitution` only.
- **Constitution, appeals lifecycle** — the constitution is the one that is off: "Upheld" must mean the initial verdict is kept, "Overturned" that it is changed (A26). The steward spec now says "Uphold initial verdict" / "Change initial verdict".
- **Core, line 59** — ten channels per division and six for the server (A6).
- **Core, "two tiers … and no other"** — four levels of authority (A9).
- **Core, line 141 / results line 17** — while stewarding is enabled, its cycle moves a round through awaiting report/appeal verdicts (A4).
- **Core, deletion on return to Not Signed Up** — a driver whose licence records any sanction is kept (A3).
- **Core, line 406** — the bar is the licence's active bans (A3).
- **README lines 269 and 663** — "eight" channel commands and channels (A6).
- **Image spec, verdict graphic and banner** — amended to carry the steward spec's verdict output (A10).
- **Results how-to, line 80** — says results, standings and verdicts "can be the same channel"; contradicts core line 60. A defect in the guide regardless of stewarding; candidate issue, not yet drafted.
- **Image spec, aspects list and "images config toggle"** — the license sheet aspect, its toggle value and "images template license" (A16).
- **Core, "A driver's accounts"** — the season ban and league ban roles move with the current account, as signed-up, division and team roles do (A20).
- **Results spec, amendment approval (#242)** — the verdicts channel is checked where any auto-rule is configured, as it is where autosack/autoreserve is set (A27).

## Parked during the walk-through

- ~~Self-correction of an outcome~~ — decided at C37: no command. Peers judge, conflict rules guard bias, appeals guard error; revoke and results amendment remain.
- ~~Several outcomes per report~~ — modelled at C2: a ballot is a steward's whole view, counted whole; one verdict per report listing each involved driver's outcome.
- **Bans for drivers in several divisions** — a qualifying or race ban is always served in the driver's highest tier full-time division, even where it was received in another (B12). Accepted by the user as likely unfair to such drivers, for now.
- **Configurable decision window** — the one-hour tie-break and justification timers stay fixed (C8). A setting only if a league asks for it.
- **Witnesses as a separate kind of party** — a driver brought in for footage is an involved driver, with an NFA ballot line and listed among the involved drivers (C12). A witness/party split only if leagues ask for it.
- **Attendance spec** — state that a round's check-in call, its last notice and its reserve-distribution message are deleted when the next round's check-in call is posted (the code does this; the spec is silent). The qualifying-ban notices ride along with them (C21).
- **Attendance spec, pardons** — with stewarding enabled, a league manager's pardon command replaces the penalty review's button, open from results posted until attendance points are distributed; the race-banned driver's automatic pardon (C38).
- **Attendance spec, distribution moment** — with stewarding enabled, attendance points are distributed when the report verdicts are posted (C39).
- **Core, cancelling a division or season** — while a stewarding cycle of one of its rounds stands open, it is marked cancelled only once that cycle closes (C40).
- **Results spec, disabling** — refused while a stewarding cycle is open; otherwise cascades to stewarding (C40).
- **Core, "When the bot stops"** — add the stewarding module's work to what is recovered; downtime extends the windows in which users act (C41).
- **Core, a hub channel** — a server-wide channel set by its own command (e.g. `bot-hub-channel`), open to holders of the base role, or to every member where no base role is configured, holding one panel whose options each enabled module adds; stewarding adds "View license" (any driver's licence, a public record), stats will add its own (D1).
- **Output of "View license"** — needs its own textual and image output, fields and formats to be specified in detail later (D1). Marked `<TBD>` in the spec.

## Notes for implementation — engineering, not specification (G1–G5, 2026-09-19)

The spec no longer lists the persisted ticket record field by field; that is for the code and its docstrings. When it is built, the record must at least hold, so that nothing the stages rely on is lost:
- the unique ID, and the season, division tier (frozen when minted), round, session and lap;
- the complainant (or the stewarding team, for a steward's report), the involved drivers by driver profile and the account each was named under, and for an appeal, the appellant;
- the complaint and every evidence file and link, the incident text as settled, and for an appeal the grounds as settled;
- the ticket's channel, and the messages it posted there (buttons, requests, the decision message);
- the effective stewarding team with the effective head steward, every change to it, and every exclusion;
- every ballot, whole: the steward, an outcome and infringement per involved driver, the justification, and the time it was last confirmed (C10);
- the stage it stands in, with the moment each stage began and is due to end, and every downtime extension applied (C41);
- the settled verdict, the justification as settled, whether it was withdrawn or rejected as a duplicate (C35), and the IDs of the verdict messages posted;
- tokens spent and refunded, for an appeal.
