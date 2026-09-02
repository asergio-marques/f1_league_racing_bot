# Setting up the results & standings module

Turn the results & standings module on and the bot runs your championship. At the moment each round is due to start it opens a private channel and asks you for the classification of every session, one at a time. It scores them against a points table you designed, publishes the results and the two championship tables to your drivers, walks you through your stewarding decisions, announces each verdict, and then does the whole lot again for the next round with every later standing brought up to date.

This guide is the **order to do things in**, from switching the module on to a settled, published round. It sends you to the reference for the fine print:

- **[Results Module Commands](../../README.md#results-module-commands)** in the main README — every command in full, with the exact submission formats.
- **[Module Commands](../../README.md#module-commands)** — turning modules on and off, and what each one depends on.
- **[Image Module](../../README.md#image-module)** — the three settings that turn results, standings and verdicts into pictures, and [Setting up the image module](configuring-the-image-module.md) for the order to do those in.

You do not need to read those first. Start here.

This guide covers the results & standings module only. Setting the bot up, creating a season, adding divisions, seating drivers in teams and adding rounds are a job of their own — follow **[Setting up the bot for your league](configuring-the-core-bot.md)** for those. Where results depends on one of them, it is named and linked, not explained.

---

## A note on five words

**Points configuration** — a named table saying what each finishing position is worth, kept separately for each of the four kinds of session. `100%` and `Half Points` are the sort of names leagues use. A server can hold as many as it likes, and a season can carry several so that a race cut short can be scored differently from a full one.

**Submission channel** — a channel the bot creates by itself when a round is due to start, visible only to your league managers, in which you paste each session's classification. It is where the whole round is handled, and the bot deletes it when the round is finished with.

**The three stages of a result** — every round's results are published three times, under a label saying which stage they are at. **Provisional Results** the moment you finish submitting, **Post-Race Penalty Results** once your stewarding decisions are applied, and **Final Results** once appeals are closed. The tables are replaced in place, so your drivers see one post per session that changes label as the round is settled.

**Verdict** — the announcement of one stewarding decision: who, which session, what sanction, and why. It goes to the division's verdicts channel, where your league can read it.

**Settled** — shorthand in this guide for a round that has been through both review stages and reached Final Results. Several things wait for it: attendance points, the deletion of the submission channel, and the ability to amend the round at all.

---

## Before you start

**The bot must already be set up, and you need a season with divisions, teams and seated drivers.** Results are submitted round by round and scored driver by driver, so there must be rounds to score and people to score. If you have not got that far, follow [Setting up the bot for your league](configuring-the-core-bot.md) first; this guide picks up from there.

**Set the Reserve team's role.** `/team reserve-role` is easy to skip and this module is where it bites: a reserve standing in for a team is only accepted in a submission because the bot can recognise them as a reserve. Without the role, the line is rejected and you cannot submit the session at all.

**Who is allowed to run what.** Every command below also has to be run in the bot's usual command channel.

| Commands | You need |
|---|---|
| `/module enable results` and `/module disable results` | **Administrator** |
| Everything under `/results` — configurations, amendments, syncs, reserves | **Manage Server** |
| `/round results amend` | **Manage Server** |
| `/division results-channel` and `/division standings-channel` | The bot's usual role. **Not** Manage Server |
| `/division verdicts-channel` | **Manage Server** |
| Reading the submission channel, pasting results into it, and every button in it | The bot's usual role |
| Anything under `/images` | See the image guide |

> **The people who submit results are not the people who configure them.** Everything under `/results` needs Manage Server, but the submission channel is opened to anyone holding the bot's usual role, and every button in it — including the one that applies your penalties and the one that closes the round — accepts them. Pick that role accordingly; it is the role that actually runs your race weekends.

---

## Step 1 — Switch it on

```
/module enable results
```

It can only be done while **no season is active**, so this belongs alongside your other setup, before approval.

> **Switching it off is not guarded the same way.** Enabling is refused mid-season; disabling is not, and the bot says nothing about the season that is running. Treat the decision as one you make once per season regardless — turning the module off part-way through takes attendance with it and stops results being collected at all.

Nothing happens immediately. The module needs channels to post to, a points table to score with, and an approved season to work through, which are the next three steps.

> **Turning results & standings off turns attendance off with it**, and it does so quietly — you get the one reply about results, and nothing in that reply tells you attendance went too. Only the log channel records it. Attendance also cannot be switched on until results is on, because it works out who turned up by reading your classifications. See [Setting up the attendance module](configuring-the-attendance-module.md).

---

## Step 2 — Give every division three channels

```
/division results-channel   name: Division One  channel: #div1-results
/division standings-channel name: Division One  channel: #div1-standings
/division verdicts-channel  name: Division One  channel: #div1-verdicts
```

All three are set **per division**, so a league with three divisions sets nine channels. The first carries each session's classification, the second the two championship tables, the third your stewarding verdicts. They can be the same channel if you want them to be, but the standings post is replaced after every round while the results posts accumulate, so keeping them apart reads better.

**All three block a season.** While results & standings is on, a season cannot be approved until every division has all three, and the bot names each division that is missing one. The verdicts channel is the one people forget, because penalties are the last thing on your mind while building a calendar.

> **A division copied from another does not inherit any of them.** The copy starts with none and nothing warns you at the time — it surfaces later as a season that will not approve. Set them explicitly.

---

## Step 3 — Build your points table

```
/results config add name: 100%
```

A configuration starts with **every position in every session worth nothing**. Filling it in is the whole of this step, and the commands that do it are listed in full under [Points Config Management](../../README.md#points-config-management):

| Command | Use it for |
|---|---|
| `/results config session` | One position in one session type. Fine for a correction, tedious for a whole table |
| `/results config bulk-session` | A whole session's table pasted into a box, one `position, points` per line |
| `/results config xml-import` | Several sessions at once, as pasted text or an attached file |
| `/results config fl` and `/results config fl-plimit` | The fastest-lap bonus, and how far down the order it can be won |

**There are four session types** — Sprint Qualifying, Sprint Race, Feature Qualifying and Feature Race — and each carries its own table, so a league that awards points for qualifying simply fills in the qualifying tables and one that does not leaves them at nothing. A normal weekend uses the two Feature tables; a sprint weekend uses all four.

**The fastest-lap bonus belongs to races only.** Setting it on a qualifying session is refused. The position limit is what stops a driver who finished eighteenth from taking a bonus point: set it to `10` and only the top ten are eligible.

```
/results config view name: 100%
```

Reads a configuration back to you privately. Positions worth nothing at the bottom of the table are collapsed into a single `P11+` line rather than listed one by one.

> **Nothing is checked at the moment you set it, and nothing catches it later either.** You can give second place more points than first, and the bot will take it. There is a check meant to run at `/season approve` — but on a first approval it inspects a table the season has not been given yet, finds nothing, and passes. Do not rely on it: read the table back with `/results config view` before you approve, because a wrongly built table costs you a wrong championship rather than a refused approval. See [known issues](../wip-specs/known_issues.md).

> **`/results config view` needs a season.** Between seasons there is none, and the command refuses — so a table you may want to check before starting your next season setup is unreachable until you have run `/season setup`. There is also no command that lists what configurations you hold, so keep a note of the names you chose.

---

## Step 4 — Attach configurations to the season

```
/results config append name: 100%
/results config append name: Half Points
```

Building a table does **not** put it in your season. Attaching it does, and it can only be done while the season is in setup — once approved, both `append` and `detach` are refused.

**Attach as many as you will need.** On race day the bot offers you the attached configurations as buttons and you pick one **per session**, which is how a race stopped at half distance gets scored differently from the qualifying session that preceded it. Attach one only, and the bot picks it for you without asking.

**A season cannot be approved with nothing attached.** That is the one thing this module reliably adds to approval beyond the channels — the ordering check from step 3 does not fire on a first approval.

**At approval the season takes its own private copy of every attached table.** From that moment the season is sealed off: editing the server's `100%` afterwards changes nothing about the running championship, and the copy is what every round is scored against. Changing a running season's points is a separate job, described under [Correcting something afterwards](#the-points-system-itself-mid-season), and it is deliberately harder.

> **A name you mistype is accepted and breaks approval silently.** `/results config append` does not check that the configuration exists, so a typo attaches nothing at all — and `/season approve` then fails **with no message whatever**, leaving the season in setup with no clue as to why. The same happens if you `/results config remove` a configuration that is still attached, because removing one does not detach it. Check `/season review`, which lists the attached names, against the names you actually built.

---

## Step 5 — Decide whether reserves appear in the standings

```
/results reserves toggle division: Division One
```

Reserves are **shown by default**, and this is set per division. Hiding them is a presentation choice and nothing more: a hidden reserve still scores exactly as they did, their points still count towards the team whose car they drove, and unhiding them puts them back in the table with everything they earned.

Every driver seated in a **team** appears in the drivers' table from the start of the season, on nothing, whether or not they have raced yet, and every team appears in the constructors' table on the same terms. A driver sitting in Reserve appears only once they have driven, which is the other half of why this toggle exists.

---

## Step 6 — Decide between text and pictures

Out of the box, everything this module posts is a text table. The image module turns three of them into graphics, separately:

```
/module enable images
/images config toggle aspect:Session results
/images config toggle aspect:Standings
/images config toggle aspect:Verdicts
```

`results` replaces each session's classification and `verdicts` replaces the stewarding announcements. Follow [Setting up the image module](configuring-the-image-module.md) for the order — the drawing files, the flags and the badges. Results and standings each need **two** drawing files, one per kind of table, and either half can be broken on its own.

> **`standings` posts two pictures where the text posts one message.** The driver standings go first and the constructor standings after, each with its heading and lifecycle label as message text and its table attached. They are redrawn and replaced on every occasion the textual standings were reposted before. Either championship can fail on its own: the one that failed is posted as text, that section by itself, and the one that drew is left alone — so you never read the same table twice.

> **A picture never delays or changes a result.** Scoring, standings, penalties and verdicts all happen exactly as they would with the module off; the drawing is made afterwards, and a drawing that fails falls back to the text table with the reason in the log channel.

> **A results drawing file with fewer rows than your division needs falls back to text.** Nothing refuses a driver over it — the bot would rather post the full table as text than a picture quietly missing the last two drivers. Check the row count against your biggest division and your longest calendar.

> **The standings drawing files are the exception: they refuse.** Like the attendance sheet, the driver standings file is counted before a driver is seated, and `/team assign` is turned away if the division would outgrow it — with the assignment unapplied, so nothing is half-done. The constructor standings file cannot be outgrown by seating a driver, so it is checked at `/season review` instead, alongside the round columns of both files against your longest calendar. Each file is named separately in what you are told; they are two drawings and only one of them may be the one to enlarge.

---

## Step 7 — Try it without waiting

You are not going to wait for a real race to find out whether your points table is right.

```
/test-mode toggle
/test-mode roster add ...
/test-mode advance
```

`advance` fires the next thing due, and when that is a round's start it opens the submission channel there and then, in the real channel, with the real wizard. Nothing about it is a simulation except the drivers.

Two things about test mode matter here specifically. **Enabling it attaches points configurations if your season has none** — it creates and attaches `Standard` and `Half Points`, so a season can acquire a points table as a side effect of flipping a flag. And `/test-mode roster list` gives you the mention strings for your fake drivers, which is the only practical way to type a classification for twenty of them.

See [Test mode](test-mode.md) for the whole picture.

> **Test mode is only available before your league has real drivers.** The bot refuses to turn it on while any driver is signed up, unassigned, assigned or banned, and while it is on no real driver may sign up or be placed. Former drivers from a finished season do not stand in the way. Do this step before you open signups.

> **A round that has been submitted but not settled blocks `/test-mode advance`.** Finish the penalty and appeals stages first; the refusal tells you which round is waiting.

---

## What race day looks like

**The submission channel appears at the round's scheduled start time**, named for the season, the division and the round, and visible only to holders of the bot's usual role.

> **Nobody is usefully told about it.** The opening message pings the **division's** role — your drivers — in a channel your drivers cannot see, so the mention reaches nobody and your league managers get no notification at all. Watch for the channel appearing, or check after each race; do not wait to be told.

**The bot asks for one session at a time, in order** — Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race, skipping the two sprint sessions for a weekend that has none. You cannot skip ahead or go back; each session is asked for, taken, and then the next one is asked for. The exact format for each is in [Submission format — Race session](../../README.md#submission-format--race-session) and [Submission format — Qualifying session](../../README.md#submission-format--qualifying-session), and the bot repeats it above every prompt.

**Paste the whole classification as one message**, one driver per line. What the bot insists on:

- Every driver **mentioned**, and seated in that division. A name typed as text is not a driver.
- Every team **mentioned as its role**, and never the Reserve role — a reserve is submitted under the team whose car they drove.
- **No more than two lines per team**, counting a reserve standing in for it.
- **Positions running 1, 2, 3… with no gaps**, and the order of outcomes respected: classified runners first, then lapped ones, then DNF, then DNS, then DSQ.
- **A driver under the team they are seated in.** Reserves are the exception, and a wide one: a reserve may be submitted under any real team. But not a *different* one in different sessions of the same round — a driver's team must agree across every session of a round, and a submission disagreeing with one already recorded is rejected, naming the driver and the team the earlier session recorded.
- **On a qualifying session, a tyre the game actually offers** — `Soft`, `Medium`, `Hard`, `Intermediate` or `Wet`, and nothing else. Write them however suits you: case, spacing and punctuation are ignored, and each answers to its plural and its single letter, so `softs`, `S`, `Inter` and `ExWets` are all understood. Whichever you type, the bot records the proper name and draws the right tyre on the graphic. Leave the column blank, or put `N/A`, where you did not note one — that is not an error.

**A rejected block is explained line by line and asked for again.** Nothing is lost, you correct the message and paste it again, and the raw text of both the rejection and the acceptance goes to the log channel with the season, division, round and session named — which is what you go back to when somebody disputes what was submitted.

**A session that was never run is typed as `CANCELLED`.** That records the session as cancelled, posts a note to the results channel saying so, and moves on. A round where you cancel every session finishes there and then: the channel closes, and no results, standings or review follow.

**Then you choose the points configuration for that session**, from a button for each one attached to the season — or, when only one is attached, the bot picks it for you and says which it chose.

**When the last session is in, the round is published as Provisional Results.** The classifications go to the results channel and both championship tables to the standings channel, and the submission channel turns into the penalty review described next.

> **A restart mid-submission costs you everything you had already pasted for that round.** The bot deletes the part-finished channel, discards the sessions already submitted, and opens a fresh submission from the first session, telling the log channel it has done so. Sessions from other rounds are untouched.

---

## Settling the round: penalties, then appeals

The submission channel stays open, and there are **two** stages to get through. Both are worked entirely from buttons; anything else typed in the channel is deleted with an explanation.

### Stage one — post-race penalties

The prompt carries five buttons:

| Button | What it does |
|---|---|
| **➕ Add Penalty** | Pick a session, then give the driver, the sanction, a description and a justification |
| **No Penalties / Confirm** | Move to approval with nothing applied. If you have anything staged, it asks whether you really mean to discard it |
| **✅ Approve** | Commit what you have staged, immediately. With nothing staged it refuses and points you at **No Penalties / Confirm** — the button is never greyed out |
| **🔄 Resubmit Initial Results** | Throw the whole round's submission away and start collecting it again from the first session |
| **🏳️ Attendance Pardon** | Only useful with the attendance module on — see [its guide](configuring-the-attendance-module.md) |

Each staged penalty also gets its own **Remove** button, so you can take one back without clearing the list.

**A sanction is `DSQ` or a number of whole seconds**, positive or negative — `+5s` for the usual thing, `-3s` to give time back, `DSQ` to drop a driver to the bottom of the classification. Qualifying sessions take `DSQ` only. The bot refuses a negative penalty larger than the penalties that driver actually holds, and one that would leave them with a negative race time. Fractions of a second are refused: five and a half seconds cannot be given here.

**Both the description and the justification are published.** They go into the verdict your whole league reads, so write them as though the driver will quote them back at you — because they will. This is the opposite of an attendance pardon, whose justification is never shown to anyone.

**Approving is one click.** **✅ Approve** commits there and then, with no second confirmation: it applies every penalty, recomputes positions, times and points for the sessions affected, republishes the round as **Post-Race Penalty Results**, brings every later round's standings up to date, and posts one verdict per decision to the verdicts channel. Check the staged list before you press it, because nothing will ask you again.

The second prompt you may have seen — **✏️ Make Changes** alongside **✅ Approve** — belongs to **No Penalties / Confirm**, not to approval. It is what the bot shows when you say you are finishing with nothing applied, so that a list you had staged is not discarded without being asked about.

**This is also the moment attendance charges anybody.** If you run the attendance module, approving this stage is what closes the round's attendance, posts the sheet and fires any automatic sanction — which is exactly why the module waits this long.

### Stage two — appeals

A second prompt appears in the same channel, with the same shape: **➕ Add Correction**, **No Changes / Confirm**, **✅ Approve**, and a Remove button per correction. A correction takes the same values as a penalty and is the place to undo one on appeal — a `-5s` against a driver who was given five seconds unfairly, or a `DSQ` upheld.

**Approving here finishes the round.** It republishes everything as **Final Results**, posts a verdict for each correction, updates every later standing, and **deletes the submission channel**. As in stage one, **✅ Approve** commits on the first click, and **No Changes / Confirm** is the one that asks again if you have corrections staged.

**A round with nothing to appeal is one click** — **No Changes / Confirm** — and most rounds will be.

> **Nothing about the round can be amended until both stages are done.** `/round results amend` refuses a round that has not reached Final Results and tells you so.

---

## How the standings are worked out

**Points come from the table you attached, by finishing position.** A driver who did not start, or was disqualified, scores nothing. A driver who retired scores no position points but can still take the fastest-lap bonus, which is deliberate.

**The fastest-lap bonus goes to the lowest lap time in the block you submitted**, provided that driver finished inside the position limit. Where two drivers share the identical time, put `FL: @Driver` on its own line above the classification to say who gets it — see [Fastest-lap tie-breaking](../../README.md#fastest-lap-tie-breaking--fl-override-header). Without it, the tie falls to whichever of them finished higher. Disqualifying the holder forfeits the bonus and gives it to nobody.

**Both tables rank on points first**, then on a countback: most wins, then most seconds, then most thirds, and so on. If two are still level, the one who reached the higher position **first** wins the tie.

**The countback only counts Feature Race finishes.** A sprint win, and a pole position, add their points and nothing more — they are invisible to the tie-break. Only classified finishes count, so a retirement from second place is not a second place.

**Team points follow whoever drove the car.** A reserve standing in for one team in round three and another in round four sends their points to the first in round three and to the second in round four, exactly as their line said.

**Every standing is stored per round**, which is why amending round two can correct rounds three onwards without you resubmitting them, and why `/season complete` can write each driver's final position into their history.

---

## Correcting something afterwards

### A classification that was wrong

```
/round results amend division_name: Division One  round_number: 3
```

Opens a private channel for one session of one settled round; paste the corrected classification and the bot validates it, rescores it, and republishes that round and every later standing. The format has **two extra columns** for the post-race and appeal penalties, so the corrected version carries the sanctions you had applied rather than losing them — the exact layout is under [`/round results amend`](../../README.md#round-results-amend--re-submit-results-for-a-completed-session).

There is a third parameter, `session`, offering Sprint Qualifying, Sprint Race, Feature Qualifying and Feature Race. Name it up front to go straight to that session; leave it out and the bot posts buttons to pick from. If the configuration the round was scored with is no longer attached to the season and more than one now is, it will also ask you which to rescore with.

> **You get one attempt.** A block the bot rejects, an internal failure, or five minutes of silence deletes the channel, and you re-run the command to try again. The reason for a rejection goes to the log channel rather than to the channel you are looking at, so have that open. A restart also deletes an amend channel and abandons the amendment. There is a **❌ Cancel Amendment** button if you want out deliberately, and the channel only listens to the person who ran the command — nobody else can paste into it.

### The points system itself, mid-season

Changing what a win is worth halfway through a championship is a bigger thing than correcting a classification, and the bot treats it that way. It is a four-step sequence, listed under [Mid-Season Points Amendment](../../README.md#mid-season-points-amendment):

```
/results amend toggle
/results amend session name: 100%  session: Feature Race  position: 1  points: 26
/results amend review
```

`toggle` opens a working copy of the season's tables. Every `session`, `fl`, `fl-plimit` and `bulk-session` change is staged into that copy and **changes nothing anybody can see**. `review` shows you the differences and asks you to approve or reject; approving overwrites the season's tables, **rescores every round of every division from the start of the season**, and switches amendment mode back off. Rejecting leaves the working copy alone so you can keep editing.

`/results amend revert` throws the working copy away and starts it again from the season's real tables. You cannot switch amendment mode off while changes are staged — revert or review them first, and the refusal says so.

> **Approving an amendment does not update what your league can see, and says that it has.** The reply claims the standings were recomputed and reposted; the recomputation happens and the reposting fails on every round, leaving one error per round in the bot's own log file on the host — nothing appears in your log channel, so there is no sign of it anywhere you can see. Your results and standings channels keep showing the old points until you run `/results rounds sync` and `/results standings sync` **for every division**. Do that immediately after approving, and check a channel before telling anybody the championship has been rescored.

### Posts that went missing

```
/results standings sync division: Division One
/results rounds sync division: Division One
```

Both delete what the bot posted and post it again from what it holds: the first for the standings, the second for every session of every round. Reach for them when somebody deleted a channel's history, or after a correction made outside the normal flow.

---

## What you cannot change

Worth knowing so you do not go looking for the setting.

| What | Why not |
|---|---|
| The layout and wording of the results and standings tables | Fixed. The graphics beside them are yours to draw once the image module is on |
| The order sessions are asked for | Sprint Qualifying, Sprint Race, Feature Qualifying, Feature Race. There is no way to submit them out of order or in one go |
| Submitting results by command, or outside the submission channel | There is none. The channel the bot opens is the only route in |
| Opening a submission channel yourself | There is no command for it. A round whose channel never opened cannot be given one |
| Skipping the appeals stage | Both stages always run. A round with nothing to appeal takes one click |
| Fractions of a second in a penalty | Whole seconds only, at review. A submitted in-game penalty can carry a fraction; one you add cannot |
| What a tie-break counts | Feature-race classified finishes, in order, then who got there first. Sprints and qualifying are invisible to it |
| Where verdicts go | The division's verdicts channel, alongside any automatic attendance sanctions |
| Cancelling a round once its results exist | Refused, and refused while its submission channel is open. Amend the results instead |
| Editing a running season's points tables directly | The season holds its own copy. `/results amend` is the way, and it rescores everything |
| Attaching or detaching a configuration mid-season | Refused. Decide before approval |

---

## Checklist before a season

- [ ] `/module enable results` has been run, with no season active
- [ ] Every division has a results channel, a standings channel **and** a verdicts channel, including any you created by copying another
- [ ] `/team reserve-role` is set, or no reserve can ever be submitted
- [ ] At least one points configuration exists and its tables are filled in for every session type your calendar uses
- [ ] No position is worth the same as or more than the one above it — check this yourself with `/results config view`, because approval will not
- [ ] The fastest-lap bonus and its position limit are set, or deliberately left at nothing
- [ ] Every configuration you will need is attached, and `/season review` lists the names you expect — spelled exactly as you built them
- [ ] Reserve visibility is what you want, per division
- [ ] If you want pictures: the image module is on, the aspects are toggled, and both drawing files of each pair have rows enough for your biggest division — remembering that `standings` does not draw anything yet
- [ ] You have taken one full round through submission, penalties and appeals with `/test-mode advance`

---

## If something looks wrong

| What you see | Usually means |
|---|---|
| `/season approve` never replies at all | Known: a configuration is attached under a name that does not exist — a typo, or one you removed. Check `/season review` against your real names |
| Season refused for a missing channel | A division is short of its results, standings or verdicts channel. The reply names each one |
| A championship scoring second place above first | Known: the ordering check at `/season approve` does not fire on a first approval, so a wrongly ordered table gets through. Read it back with `/results config view` before you approve |
| Season refused for having no points configuration | Nothing is attached. `/results config append` first |
| No submission channel when a round started | The module is off, the division has no results channel, or the round was cancelled. The log channel says which |
| No submission channel for a round you moved | Known: `/round amend` cancels the round's submission and only puts it back if the weather module is enabled. With weather off, that round can never be submitted |
| A submission rejected over a team role | The Reserve role in a team column, three lines under one team, or a driver under a different team from the one another session of the round already records |
| A submission rejected over a driver | Not mentioned, or not seated in that division. A reserve also needs `/team reserve-role` set |
| Everything you pasted gone after a restart | Known: a part-finished submission is discarded and reopened from the first session |
| Results posted but no standings | Every session of the round was cancelled, so there was nothing to score |
| Points on the tables you did not expect | The session was scored against whichever configuration was chosen for it. `/results config view` shows what that configuration says |
| A driver keeping their fastest-lap point after retiring | Intended. A retirement forfeits position points, not the bonus |
| A sprint winner losing a tie-break | Intended. Only feature-race finishes are counted back |
| Standings still showing provisional numbers | The round has not been through both review stages. The label on the post says which stage it is at |
| Attendance charged later than expected | It is charged when the penalty stage is approved, never at provisional results |
| `/round results amend` refused | The round has not reached Final Results yet |
| An amendment that vanished | Known: it is one attempt. A rejection, a failure or five minutes of silence deletes the channel; the log channel holds the reason |
| Nothing changed in your channels after an approved amendment | Known: the rescoring works and the reposting does not, whatever the reply says. Run `/results rounds sync` and `/results standings sync` for every division |
| `/results amend toggle` refused | Changes are staged. Revert or review them first |
| Text where you expected a picture | The table worked and the drawing did not — often a drawing file with fewer rows than the division needs. The log channel names the reason |
| `/test-mode advance` refused | A round is submitted but not settled. Finish its penalty and appeals stages |
