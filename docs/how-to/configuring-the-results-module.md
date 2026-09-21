# Setting up the results & standings module

Turn the results & standings module on and the bot runs your championship. At the moment each round is due to start it opens a private channel and asks you for the classification of every session, one at a time. It scores them against a points table you designed, publishes the results and the two championship tables to your drivers, walks you through your stewarding decisions, announces each verdict, and then does the whole lot again for the next round with every later standing brought up to date.

This guide is the **order to do things in**, from switching the module on to a settled, published round. It sends you to the reference for the fine print:

- **[Results Module Commands](../../README.md#results-module-commands)** in the main README — every command in full, with the exact submission formats.
- **[Module Commands](../../README.md#module-commands)** — turning modules on and off, and what each one depends on.
- **[Image Module](../../README.md#image-module)** — the settings that turn results, standings and verdicts into pictures and head a batch of verdicts with a banner, and [Setting up the image module](configuring-the-image-module.md) for the order to do those in.

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
| `/module enable results` and `/module disable results` | The **league admin role** |
| `/results config remove`, `/results amend review` and `/round results amend` | The **league admin role** |
| Everything else under `/results` — configurations, amendments, syncs, reserves | The **interaction role** |
| `/division results-channel`, `/division standings-channel` and `/division verdicts-channel` | The **interaction role** |
| Reading the submission channel, pasting results into it, and every button in it | The **interaction role** |
| Anything under `/images` | See the image guide |

> **Three of these cannot be undone, and they ask for more.** Deleting a points configuration removes it outright, and detaches it from a season whose placements are not yet confirmed — the bot names that season and asks first, and the season then needs another configuration before it can be approved. A season already approved keeps its own copy and is unaffected. Approving a mid-season points amendment overwrites the season's points entire. Amending a round that has already gone final overwrites the classification your drivers raced — it does not keep the old one. Those three ask for the league admin role; the rest of `/results` asks for the interaction role, which is the role that actually runs your race weekends.
>
> The submission channel is opened to both roles, and every button in it — including the one that applies your penalties and the one that closes the round — accepts either. Pick the interaction role accordingly.

---

## Step 1 — Switch it on

```
/module enable results
```

It is refused once a season's **placements are confirmed**, so this belongs alongside your other setup — with no season, or before you confirm placements.

> **Switching it off is not guarded the same way — it is worse than that.** Enabling is refused mid-season; disabling is not, and deliberately so: a league whose results have become unworkable must be able to stop running them. But **disabling mid-season deletes that season's results.** Every classification you have recorded goes, every standing computed from them goes, and every results and standings message already posted is removed from its channel. Every round still waiting on results, report verdicts or appeal verdicts is closed as having run without results — which is what lets you complete the season afterwards instead of being stuck with it for ever. Every penalty and appeal verdict you have already announced is removed from the verdicts channel too, with the banner above it: the decisions go with the results they were made on. An auto-sack or auto-reserve announcement stays, being the attendance module's, and so does a banner heading one. An amendment still open is ended and its channel deleted. Your points configurations and your division channels are kept, and a round whose date has not yet come is left alone until it passes.
>
> The bot warns you and writes nothing until you confirm, whether or not attendance is on. **None of it can be undone**, you will not get either module back until the season ends, and the championship your drivers raced is simply gone. Treat the decision as one you make once per season.

Nothing happens immediately. The module needs channels to post to, a points table to score with, and an approved season to work through, which are the next three steps.

> **Turning results & standings off turns attendance off with it.** Where attendance is on, the bot stops and warns you before it does anything: it names what the cascade will take — every check-in still to come, and every division's check-in and attendance channels — and switches nothing off until you confirm. The reply that follows names both modules, and the log channel records the cascade as well. Attendance also cannot be switched on until results is on, because it works out who turned up by reading your classifications. See [Setting up the attendance module](configuring-the-attendance-module.md).

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

> **A lower position may never be worth as much as the one above it.** Give second place more points than first and the bot takes the change, then tells you the table is out of order and names the positions. The edit is not refused, because filling a table in passes through states that are momentarily wrong — setting second place before first, or repairing a table from the bottom up — and refusing them would make ordinary ways of building a table impossible to follow.
>
> **Approval is where it is refused**, and `/season placements-review` names it before you get there — the report lists every position at fault and the Approve button is withheld, so you are not offered an approval that would be turned down. Two positive values tying counts as out of order; positions worth nothing at the bottom of the table do not, being the ordinary shape of one.

> **`/results config view` needs a season.** Between seasons there is none, and the command refuses — so a table you may want to check before starting your next season setup is unreachable until you have run `/season setup`. There is also no command that lists what configurations you hold, so keep a note of the names you chose.

---

## Step 4 — Attach configurations to the season

```
/results config append name: 100%
/results config append name: Half Points
```

Building a table does **not** put it in your season. Attaching it does, and it can only be done before the season's placements are confirmed — once approved, both `append` and `detach` are refused.

**Attach as many as you will need.** On race day the bot offers you the attached configurations as buttons and you pick one **per session**, which is how a race stopped at half distance gets scored differently from the qualifying session that preceded it. Attach one only, and the bot picks it for you without asking.

**A season cannot be approved with nothing attached**, nor with an attached name that no longer exists, nor with a table that is out of order. Those are the three things this module adds to approval beyond the channels, and all are checked against the configurations as they stand when you press Approve — not against the season's copy, which is taken only once the approval has passed every gate.

**At approval the season takes its own private copy of every attached table.** From that moment the season is sealed off: editing the server's `100%` afterwards changes nothing about the running championship, and the copy is what every round is scored against. Changing a running season's points is a separate job, described under [Correcting something afterwards](#the-points-system-itself-mid-season), and it is deliberately harder.

> **A name you mistype is refused on the spot.** `/results config append` checks the configuration exists before attaching anything, so a typo is told to you at the moment you make it rather than becoming a season that cannot be approved. If you have an older season carrying such a name already, `/season placements-review` names it and approval refuses on it, saying which one is missing and how to put it right.

---

## Step 5 — Decide whether reserves appear in the standings

```
/results reserves toggle division: Division One
```

Reserves are **shown by default**, and this is set per division. Hiding them is a presentation choice and nothing more: a hidden reserve still scores exactly as they did, their points still count towards the team whose car they drove, and unhiding them puts them back in the table with everything they earned.

Every driver seated in a **team** appears in the drivers' table from the start of the season, on nothing, whether or not they have raced yet, and every team appears in the constructors' table on the same terms. A driver sitting in Reserve appears only once they have driven, which is the other half of why this toggle exists.

> **Decide before the last round.** The toggle is refused once every division of the season is done — with the racing over, the display is settled and the final classification is drawn next. It is refused on a completed or cancelled season too.

---

## Step 6 — Decide between text and pictures

Out of the box, everything this module posts is a text table. The image module turns three of them into graphics and adds a fourth of its own, each switched separately:

```
/module enable images
/images config toggle aspect:Session results
/images config toggle aspect:Standings
/images config toggle aspect:Verdicts
/images config toggle aspect:Verdict banner
```

`results` replaces each session's classification and `verdicts` replaces the stewarding announcements. `Verdict banner` replaces nothing — it *adds* a header above the run of verdicts a review produces, naming the round they were taken on. Follow [Setting up the image module](configuring-the-image-module.md) for the order — the drawing files, the flags and the badges. Results and standings each need **two** drawing files, one per kind of table, and either half can be broken on its own.

> **The verdict banner comes at a cost worth weighing.** Its message carries no words at all, so
> searching your verdicts channel for "Round 8" stops finding that round's verdicts — the attachment's
> filename is all a search has to go on. Leave it off if your stewards look decisions up that way.
> The full trade-off is under *Step 6* of [Setting up the image module](configuring-the-image-module.md#step-6--choose-which-outputs-become-pictures).

> **`standings` posts two pictures where the text posts one message.** The driver standings go first and the constructor standings after, each with its heading and lifecycle label as message text and its table attached. They are redrawn and replaced on every occasion the textual standings were reposted before. Either championship can fail on its own: the one that failed is posted as text, that section by itself, and the one that drew is left alone — so you never read the same table twice.

> **A picture never delays or changes a result.** Scoring, standings, penalties and verdicts all happen exactly as they would with the module off; the drawing is made afterwards, and a drawing that fails falls back to the text table with the reason in the log channel.

> **A results drawing file with fewer rows than your division needs falls back to text.** Nothing refuses a driver over it — the bot would rather post the full table as text than a picture quietly missing the last two drivers. Check the row count against your biggest division and your longest calendar.

> **The standings drawing files are the exception: they refuse.** Like the attendance sheet, the driver standings file is counted before a driver is seated, and `/team assign` is turned away if the division would outgrow it — with the assignment unapplied, so nothing is half-done. The constructor standings file cannot be outgrown by seating a driver, so it is checked at `/season placements-review` instead, alongside the round columns of both files against your longest calendar. Each file is named separately in what you are told; they are two drawings and only one of them may be the one to enlarge.

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

> **Test mode is chosen for a season, in its configuration.** `/test-mode toggle` works only while a season is in configuration, and is refused while any real driver is signed up, unassigned or assigned — which, once a season has ended, nobody is. A season confirmed in test mode never opens a signup window, and while test mode is on no real driver may sign up or be placed. It stays on until that season is completed, cancelled or aborted, which switches it off and deletes the fake drivers — so test in a season of its own, and abort it with `/season abort` if you would rather not race it out. See [Setting up the bot for your league](configuring-the-core-bot.md) for the season's stages.

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

**Cancelling a whole round is a different thing, done with `/round cancel`.** The results channel then gets a silent note that no results are coming for it — it sits in the channel and pings nobody — and no submission channel opens. `/division cancel` and `/season cancel` post the same note for everything still to come. The note is posted only while this module is on.

**A session that was never run is typed as `CANCELLED`.** That records the session as cancelled, posts a note to the results channel saying so, and moves on. A round where you cancel every session finishes there and then: the channel closes, and no results, standings or review follow.

**Then you choose the points configuration for that session**, from a button for each one attached to the season — or, when only one is attached, the bot picks it for you and says which it chose.

> **Nothing is taken while another round of the division is being amended.** A paste, a `CANCELLED` and both review approvals are refused, naming the round and the amend channel — the channel stays open, and nothing you staged is lost. Try again once the amendment has finished: it ends when approved, or is undone once half an hour has passed since its corrections were pasted, give or take the few minutes the bot takes to notice. Your submission posts standings, and they would otherwise publish corrections nobody has approved yet.

**When the last session is in, the round is published as Provisional Results.** The classifications go to the results channel and both championship tables to the standings channel, and the submission channel turns into the penalty review described next.

> **A restart mid-submission costs you everything you had already pasted for that round.** The bot deletes the part-finished channel, discards the sessions already submitted, and opens a fresh submission from the first session, telling the log channel it has done so. Sessions from other rounds are untouched.

---

## Settling the round: penalties, then appeals

The submission channel stays open, and there are **two** stages to get through. Both are worked entirely from buttons; anything else typed in the channel is deleted with an explanation, except while you are resubmitting the round's results.

### Stage one — post-race penalties

The prompt carries five buttons:

| Button | What it does |
|---|---|
| **➕ Add Penalty** | Pick a session, then give the driver, the sanction, a description and a justification |
| **No Penalties / Confirm** | Move to approval with nothing applied. If you have anything staged, it asks whether you really mean to discard it |
| **✅ Approve** | Commit what you have staged, immediately. With nothing staged it refuses and points you at **No Penalties / Confirm** — the button is never greyed out |
| **🔄 Resubmit Initial Results** | Start collecting the whole round again from the first session. The results already submitted stay in place until the last session is in |
| **🏳️ Attendance Pardon** | Only useful with the attendance module on — see [its guide](configuring-the-attendance-module.md) |

Each staged penalty also gets its own **Remove** button, so you can take one back without clearing the list.

**Resubmitting keeps the round's results until you have replaced them.** Pressing it discards anything you have staged, takes the prompt down, and asks for every session again in the same channel, the same way as the first time. Until the last session is in, the results, tables and standings the league is looking at are the ones you already submitted — then the new ones replace them in one go and are published as provisional again, marked as amended. Changed your mind part-way? The announcement carries a **Cancel** button: it stops the resubmission, leaves the earlier results exactly as they were, and brings the prompt back. The penalties you had staged do not come back with it.

> **A restart during a resubmission loses what you had pasted, not the round's results.** The earlier results stand and the prompt comes back, with a note in the channel. Press **🔄 Resubmit Initial Results** again to start over.

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

**Both tables rank on points first**, then on a countback: most wins, then most seconds, then most thirds, and so on. If two are still level, the one who reached the higher position **first** wins the tie, and after that a driver who has raced at all ranks above one who has not.

**When even that separates nobody, the order is stated rather than raced for.** Two drivers on nought at the start of a season is the ordinary case. They are ordered alphabetically by team — the reserve team after every named team, and a driver who holds no seat in the division after the reserves — then alphabetically by driver within the team, drivers being ordered on the name the table draws them under. Where two drivers carry the same name, or two teams do, the smaller Discord ID goes first, which is what stops the order coming out differently from one posting to the next.

**The countback only counts Feature Race finishes.** A sprint win, and a pole position, add their points and nothing more — they are invisible to the tie-break. Only classified finishes count, so a retirement from second place is not a second place.

**Team points follow whoever drove the car.** A reserve standing in for one team in round three and another in round four sends their points to the first in round three and to the second in round four, exactly as their line said.

**Every standing is stored per round**, which is why amending round two can correct rounds three onwards without you resubmitting them, and why `/season complete` can write each driver's final position into their history.

**The standings channel also gets a posting at each end of the season.** Approving the season posts an **opening classification** — every driver and team on zero, with the calendar drawn empty beside them — and `/season complete` posts a **final classification**, holding the last round that has results. Both go to the same channel as every round's standings, as drawings where you turned pictures on and as the ordinary text tables where you did not. Nothing has been scored when the opening one goes out, so there is no championship to order it by: it comes out under the tie-break above, by team and then by driver.

Neither is a round's standings, so neither is replaced by anything and neither is touched by `/results standings sync`, which walks the rounds that have results. If a division's cannot be posted, the log channel says so, the other divisions carry on, and the season is approved or completed regardless.

---

## Correcting something afterwards

### A classification that was wrong

```
/round results amend division_name: Division One  round_number: 3
```

Opens a private channel for one settled round — as many of its sessions as need correcting — and **walks you back through the round the way you raced it**: the classification, then the reports, then the appeals. It is three steps rather than one paste, and you are not finished until the third is approved.

**Step one — the classification.** Paste the corrected results. Correcting several sessions, the bot asks for each in turn, in running order, and writes nothing until the last paste is in. The format is the same as a first submission: the exact layout is under [`/round results amend`](../../README.md#round-results-amend--re-submit-results-for-a-completed-session). If you have a paste saved from before, note that the **two extra sanction columns have been withdrawn** — the bot will refuse a block that still carries them and tell you why. Sanctions are not pasted any more; you review them in step two.

**Step two — the reports.** The bot lists the penalties the sessions you chose already carry, all of them together, and gives you a button beside each. Keep them, change them, remove them, add new ones. This is also the only place to amend the round's **attendance pardons**, which get a **Remove Pardon** button each. Approving without touching anything leaves every decision exactly as it stood, so a correction to one driver's lap time costs you nothing here. There is no **🔄 Resubmit Initial Results** button in an amendment: if the classification itself needs redoing, cancel and run the command again.

**Step three — the appeals.** The same again for the appeals. Approving this last step is what commits the amendment and rebuilds the channels.

There is a third parameter, `session`, offering Sprint Qualifying, Sprint Race, Feature Qualifying and Feature Race. Name it up front to amend just that session; leave it out and the bot asks which to amend, and you can pick several. Pick every session whose classification — or whose penalties and appeals — you want to change: the ones you leave out are not reopened. If the configuration the round was scored with is no longer attached to the season and more than one now is, it will also ask you which to rescore with.

> **Nothing is published until you finish.** The corrected classification is recorded as you paste it, but your drivers keep reading the round as it was raced until you approve the appeals — the round is never shown to them half-amended. And the sessions you did not choose are left alone: their penalties and appeals are not reopened and not re-applied.

> **What you approve is what the round carries.** Each stage rewrites the round's decisions rather than adding to them: keep a report and it stays as it was, remove one and it is gone, and amending the same round twice leaves it as the second amendment settled it rather than doubling the first. That is why the stages show you everything the round already holds before you approve — what you leave alone is written back out with its justification and its author intact.

> **Sanctions keep their reason.** A penalty follows its driver onto the corrected classification with the justification, the author and the time it was given intact — that is why they are reviewed rather than re-typed. If your corrected classification leaves a driver out who carries a penalty or an appeal, the amendment is refused and names them. Put the driver back in, or have the verdict withdrawn first.

> **Finish what you start, or it is undone.** The first step commits — the corrected classification is written and the round scored from it before you review the reports and appeals. So an amendment left unapproved does not simply sit there: half an hour after the corrected results are recorded, the bot puts the round back exactly as it was and says so in the log channel. That half hour covers the reports *and* the appeals — approving the reports does not restart it — so work out your decisions before you run the command. **❌ Cancel Amendment** does the same at any point before you approve the appeals, as does a restart or a step that fails part-way. Nothing needs reposting, because nothing was posted; you can run the command again, but do not paste the classification and come back tomorrow expecting to carry on.

> **One amendment open in a division at a time.** While any round of the division has one open, running the command for the division again is refused and names the round and the channel the open one is in. Finish or cancel that first. To correct several sessions of one round, choose them all in one amendment.

> **An amendment holds up the rest of its division.** Until it is finished, the submission channels of the division's other rounds refuse results and approvals, and the division's two sync commands are refused — and in any division, a points change cannot be approved with `/results amend review`. Race day in the same division waits for you, so do not start one just before a round's results are due.

> **You get one attempt at the paste.** A block the bot rejects, an internal failure, or five minutes of silence deletes the channel, and you re-run the command to try again. Amending several sessions, that means the whole amendment: a refused paste throws away the ones already accepted, so have every session's classification checked and ready before you start. The reason for a rejection goes to the log channel rather than to the channel you are looking at, so have that open. A restart deletes an amend channel and undoes the amendment at whatever step it had reached. There is a **❌ Cancel Amendment** button if you want out deliberately, and the channel only listens to the person who ran the command — nobody else can paste into it.

> **Expect the whole division to be reposted, and expect it to take a minute.** Once you approve the appeals, every round of the division goes up again in round order across the results, standings and verdicts channels, with the attendance sheet reposted beside them. That is deliberate: a repost is a new message at the bottom of the channel, so replacing only round 1 of five would leave the channel reading 2, 3, 4, 5, 1. The new messages are posted **before** the old ones come down, so for up to a minute you will see both — that is the bot working, not a fault, and it resolves itself. Anything it could not repost is named in the log channel under `RESULT_AMENDED | Incomplete`.

> **Old verdict announcements are replaced, banners and all.** Every verdict records which message it went in, and every round's run of verdicts which banner heads it, so an amendment can take them down and post the corrected ones. A round's old announcements only come down once all of its replacements are up: if one could not be posted, the originals are left standing, and the `RESULT_AMENDED | Incomplete` entry names them with a link to each so you can remove them yourself once the verdict is posted. The bot will not pretend it replaced something it did not. A round you have taken every verdict out of loses its old cards and their banner; a banner that also heads an auto-sack or auto-reserve card stays, because the amendment leaves those cards where they are.

### The points system itself, mid-season

Changing what a win is worth halfway through a championship is a bigger thing than correcting a classification, and the bot treats it that way. It is a four-step sequence, listed under [Mid-Season Points Amendment](../../README.md#mid-season-points-amendment):

```
/results amend toggle
/results amend session name: 100%  session: Feature Race  position: 1  points: 26
/results amend review
```

`toggle` opens a working copy of the season's tables. Every `session`, `fl`, `fl-plimit` and `bulk-session` change is staged into that copy and **changes nothing anybody can see**. `review` shows you the differences and asks you to approve or reject; approving overwrites the season's tables, **rescores every round of every division from the start of the season**, reposts each of those rounds in the division's results and standings channels, and switches amendment mode back off. Rejecting leaves the working copy alone so you can keep editing.

`/results amend revert` throws the working copy away and starts it again from the season's real tables. You cannot switch amendment mode off while changes are staged — revert or review them first, and the refusal says so.

> **Mid-season means mid-season.** Every command of the group is refused once the season is **pending completion** — with every division done the points are settled and the final classification is drawn next — and on a completed or cancelled season, which is an archive. One consequence to know before you start: amendment mode left switched **on** when the last division finishes cannot be switched off, because `toggle` is refused too. That stops nothing — the season still completes and the staged changes are simply never applied — but revert or approve before the last round if you would rather not leave it that way.

> **An amendment that would leave the points out of order cannot be approved.** The same rule approval holds at the start of a season holds here, and for the same reason: an approved amendment rescores every round of every division against the new table at once. `review` shows the problem alongside the diff, and pressing Approve refuses and changes nothing — the working copy is left as it is, so you can repair it and review again. Each `amend session` or `amend bulk-session` that breaks the ordering warns you at the moment you stage it.

> **Approving reposts every round that has been raced, and only those.** A round still to come is left alone, and a round that has been raced comes back under the label it already stood at — amending the points does not push a round at Final Results back to provisional. Expect a burst of posting across every division's channels: one round at a time, from the first round of the season, which on a long calendar takes a moment to work through.

> **An amendment the bot cannot publish is refused outright.** Because approving rescores and reposts every division at once, it checks first that it can do all of it: every division's results and standings channels still present and writable, and with attendance on, its attendance channel too — plus the verdicts channel if you use autosack or autoreserve. A channel you have never set is fine and is skipped. A channel that has been **deleted** stops the approval, and so does one the bot cannot post in: it needs **View Channel**, **Send Messages** and **Read Message History** on every one of them — the last because a repost replaces what it posted before rather than adding to it — and **Attach Files** as well on any channel that receives pictures, which means the results and standings channels when you have the images module on with those aspects switched on. The refusal names the permission that is missing, in the same words the channel's own settings use. When it stops: `review` names the division and the channel alongside the diff, pressing Approve refuses, and nothing at all changes — the season keeps its points, your staged changes stay staged, and amendment mode stays on. Set the channel again with `/division results-channel` or `/division standings-channel` and run `/results amend review` again.
>
> This is the one place in the bot that behaves the *opposite* way to the opening and final classifications described above, which carry on and report what they could not post. An amendment overwrites the whole season's scoring, so it is all-or-nothing by design: you never end up with half your divisions showing the new points and half the old.
>
> The attendance sanctions an approval can set off are the exception. With attendance on and a threshold set, a sanction that fails to apply does not undo the approval — the reply lists it with the `/attendance sync` command that finishes it. The attendance guide explains what to do.

> **Not while a round's results are being amended.** Approving reposts every division, so while any of them has a `/round results amend` open, `review` names that round and its channel alongside the diff, and pressing Approve refuses and changes nothing. Review again once the amendment has finished.

### Posts that went missing

```
/results standings sync division: Division One
/results rounds sync division: Division One
```

Both delete what the bot posted and post it again from what it holds: the first for the standings, the second for every session of every round. Reach for them when somebody deleted a channel's history, or after a correction made outside the normal flow.

> **Both are refused once every division is done,** and on a season already completed or cancelled. While a season is pending completion the one thing that still reposts is `/round results amend` — see the next section.

> **Both wait while the division has an amendment open.** They repost from what the bot holds, and that includes the amendment's corrections before you have approved them. The refusal names the round and its amend channel; run them again once it has finished.

### A repost the bot told you it could not make

Approving either review stage, and `/round results amend`, republish the round's results and every later round's standings. When any of that cannot be posted, you are told about it rather than left to find an empty channel.

- **Approving a review stage** tells you twice: in your own reply to the approval, and in the log channel as a `RESULTS_REPOST | Incomplete` entry. The approval's own log line reads `Incomplete` rather than `Success`.
- **`/round results amend`** has no button and nobody to reply to, so it reports only to the log channel, inside its own `RESULT_AMENDED | Incomplete` entry.

Work it in this order.

1. **Read what you are told.** Most lines name the division and the channel at fault; a few say instead that the server itself could not be reached, or list the later rounds whose standings were left alone. Every report ends with the exact commands to run — the two syncs while the season is being raced, or, where every division is already done and the syncs are closed, the `/round results amend` to run again.
2. **Repair the cause.** Usually the channel has been deleted, or the bot's **View Channel**, **Send Messages** or **Read Message History** has been taken away on it. Where the channel is gone for good, point the division at a new one with `/division results-channel` or `/division standings-channel` first.
3. **Run whatever the report named.** Only then — running it before the cause is repaired fails the same way. With the season still being raced that is the two sync commands. With every division done it is `/round results amend` for the round, which replaces the round's own results *and* every later round's standings, so it recovers the same ground.

**What is already posted is left alone when this happens.** The bot checks the channel before it deletes anything, so a failure leaves the league reading the version from before the approval rather than an empty channel. That version is out of date until you run the syncs, which is why the entry is worth acting on the same day.

**The approval itself went through.** The penalties are applied, the championship is recalculated and the round has moved on. Only the posting is outstanding — do not approve anything a second time to try to fix it.

### A verdict that was not announced

The verdict in your verdicts channel is the only thing that tells a driver *why* their classification changed. When one cannot be posted, it is named in your reply and in the log channel as a `VERDICTS | Incomplete` entry, with the driver it was owed to.

**This one you have to finish by hand.** There is no command that announces a decided verdict again — the bot keeps no record of the message, so it cannot find or replace one. So:

1. **Repair the cause.** Usually the verdicts channel has been deleted or the bot's permission to post in it has been taken away. If the channel is gone, set a new one with `/division verdicts-channel`.
2. **Post the decision yourself**, in that channel, naming the driver, the sanction and the reasoning. The entry in the log channel has the details you need.

**A division with no verdicts channel is reported, not skipped.** Unlike a results or standings channel, a verdicts channel is one of the three every division must have before its placements can be confirmed — so if a verdict cannot find one, something has been removed since.

**Automatic attendance sanctions are the same.** It is tempting to think `/attendance sync` will announce one that failed — it re-runs the sanctions, after all — but it will not. A driver already sacked or already moved to Reserve is no longer a candidate on a second run, so they are passed over silently. When the log says a sanction was *applied but not announced*, post that one by hand too.

### Attendance the approval could not record

With the attendance module on, approving a penalty review also records who attended the round. Where that fails you get an `ATTENDANCE_RECORD | Incomplete` entry and a line in your reply, and the sanctions for that round are held back rather than applied to a total the bot knows is wrong. [The attendance guide](configuring-the-attendance-module.md) explains what to do about it.

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
| Two entries level on everything | Ordered by team alphabetically, reserves last, then by driver name, then by Discord ID. Not yours to set |
| Where verdicts go | The division's verdicts channel, alongside any automatic attendance sanctions |
| Cancelling a round once its results exist | Refused, and refused while its submission channel is open. Amend the results instead |
| Editing a running season's points tables directly | The season holds its own copy. `/results amend` is the way, and it rescores everything |
| Attaching or detaching a configuration mid-season | Refused. Decide before approval |
| Turning the module off mid-season | Allowed, and it deletes the season's results — see [Step 1](#step-1--switch-it-on). You confirm first, and nothing can be undone |
| Getting the season's results back afterwards | There is no way. The module cannot even be re-enabled until the season ends |

---

## Checklist before a season

- [ ] `/module enable results` has been run, with no season active
- [ ] Every division has a results channel, a standings channel **and** a verdicts channel, including any you created by copying another
- [ ] `/team reserve-role` is set, or no reserve can ever be submitted
- [ ] At least one points configuration exists and its tables are filled in for every session type your calendar uses
- [ ] No position is worth the same as or more than the one above it — check this yourself with `/results config view`, because approval will not
- [ ] The fastest-lap bonus and its position limit are set, or deliberately left at nothing
- [ ] Every configuration you will need is attached, and `/season placements-review` lists the names you expect — spelled exactly as you built them
- [ ] Reserve visibility is what you want, per division
- [ ] If you want pictures: the image module is on, the aspects are toggled, and both drawing files of each pair have rows enough for your biggest division — remembering that `standings` does not draw anything yet
- [ ] You have taken one full round through submission, penalties and appeals with `/test-mode advance`

---

## If something looks wrong

| What you see | Usually means |
|---|---|
| Pressing **Approve** never replies at all | Known: a configuration is attached under a name that does not exist — a typo, or one you removed. Check `/season placements-review` against your real names |
| Season refused for a missing channel | A division is short of its results, standings or verdicts channel. The reply names each one |
| Season refused for a points table out of order | A position is worth as much as or more than the one above it. The refusal names each one; repair them with `/results config session` and approve again |
| Season refused for having no points configuration | Nothing is attached. `/results config append` first |
| Season refused for a points configuration that does not exist | A name is attached that the server no longer holds — a typo from an older season, or a configuration since removed. The refusal names it; build it with `/results config add` or drop it with `/results config detach` |
| No submission channel when a round started | The module is off, the division has no results channel, or the round was cancelled. The log channel says which |
| No submission channel for a round you moved | `/round amend` re-arms the round's submission whatever your modules. If one still does not open, check the round actually reached its scheduled time |
| A submission rejected over a team role | The Reserve role in a team column, three lines under one team, or a driver under a different team from the one another session of the round already records |
| A submission rejected over a driver | Not mentioned, or not seated in that division — a driver placed mid-season whose placement is not yet confirmed counts as not seated. A reserve also needs `/team reserve-role` set |
| Everything you pasted gone after a restart | Known: a part-finished submission is discarded and reopened from the first session. A part-finished *resubmission* is dropped too, but the round keeps the results it had |
| Results posted but no standings | Every session of the round was cancelled, so there was nothing to score |
| Points on the tables you did not expect | The session was scored against whichever configuration was chosen for it. `/results config view` shows what that configuration says |
| A driver keeping their fastest-lap point after retiring | Intended. A retirement forfeits position points, not the bonus |
| A sprint winner losing a tie-break | Intended. Only feature-race finishes are counted back |
| Standings still showing provisional numbers | The round has not been through both review stages. The label on the post says which stage it is at |
| Attendance charged later than expected | It is charged when the penalty stage is approved, never at provisional results |
| `/round results amend` refused | The round has not reached Final Results yet — or the division already has an amendment open, whose round and channel the refusal names |
| A paste, an approval or a sync refused, naming a round being amended | A round of the division has an amendment open. Try again once it has finished — it ends when approved, or is undone a little over half an hour after its corrections were pasted |
| An amendment that vanished | Known: it is one attempt. A rejection, a failure or five minutes of silence deletes the channel; the log channel holds the reason. Past the paste, half an hour without approval undoes it (`AMEND_REVERTED`) |
| A round left unchanged by an approved amendment | It has not been raced, so there was nothing to repost. Only rounds with results are reposted |
| `/results amend review` refuses, naming a division and a channel | That channel has been deleted, or the bot can no longer post in it. Nothing was changed — set it again with `/division results-channel` or `/division standings-channel` and review again |
| `/results amend review` refuses, naming a round being amended | A `/round results amend` is open. Nothing was changed — review again once it has finished |
| `/results amend toggle` refused | Changes are staged — revert or review them first. Or every division of the season is done, in which case the whole group is closed and the season is completed as it stands |
| Text where you expected a picture | The table worked and the drawing did not — often a drawing file with fewer rows than the division needs. The log channel names the reason |
| `/test-mode advance` refused | A round is submitted but not settled. Finish its penalty and appeals stages |
