# Setting up the attendance module

Turn the attendance module on and every round asks your drivers a question days in advance: are you racing? They answer with a button. At a deadline you choose, the answers close, the bot fills the empty seats with whichever reserves put their hand up, and once the results are settled it charges points against everyone who did not answer or did not turn up. Those points accumulate across the season, and if you want them to, they can move a driver to reserve or take them off the grid entirely without you lifting a finger.

This guide is the **order to do things in**, from switching the module on to a season running check-ins on its own. It sends you to the reference for the fine print:

- **[Attendance Module](../../README.md#attendance-module)** in the main README — every configuration command in full.
- **[Module Commands](../../README.md#module-commands)** — turning modules on and off, and what each one depends on.
- **[Image Module](../../README.md#image-module)** — the two settings that turn the check-in call and the attendance sheet into pictures, and [Setting up the image module](configuring-the-image-module.md) for the order to do those in.

You do not need to read those first. Start here.

This guide covers the attendance module only. Setting the bot up, creating a season, adding divisions, seating drivers in teams and adding rounds are a job of their own — follow **[Setting up the bot for your league](configuring-the-core-bot.md)** for those. Where attendance depends on one of them, it is named and linked, not explained.

---

## A note on four words

**Check-in call** — the message the bot posts before a round asking a division whether its drivers are racing. It pings the division role, lists every driver team by team, and carries three buttons. There is one per round per division, and the bot deletes the last one when it posts the next, so the channel never holds two.

**Attendance points** — what a driver collects for failing to answer the call or failing to appear. They have nothing to do with championship points and never touch the standings; they are a separate tally that only the attendance module reads. Fewer is better.

**Reserve distribution** — what the bot does the moment check-in closes. It works through the reserves who accepted, in the order they accepted, and puts them into the teams that need them most. Reserves it cannot seat are told they are on standby.

**Sanction** — the bot acting on a driver's attendance total by itself: moving them to the reserve team, or removing them from every seat in the league. You choose the total that triggers it, or switch it off, and you can only have one of the two.

---

## Before you start

**The bot must already be set up, and you need a season with divisions, teams and seated drivers.** Attendance works round by round and driver by driver, so there must be rounds to check into and people to ask. If you have not got that far, follow [Setting up the bot for your league](configuring-the-core-bot.md) first; this guide picks up from there.

**Seat some drivers in the Reserve team.** The bot creates it for you and puts it in every division, so there is nothing to set up — but an empty one means reserve distribution has nobody to distribute, and auto-reserve has nowhere to move anybody to. Seating drivers is core setup; see [Setting up the bot for your league](configuring-the-core-bot.md).

**Who is allowed to run what.** Every command below also has to be run in the bot's usual command channel by someone with the usual bot role.

| Commands | You need |
|---|---|
| `/module enable results` and `/module enable attendance` | The **league admin role** |
| `/attendance config` — all of them | The **interaction role** |
| `/attendance sync` | The **interaction role** |
| `/attendance post-check-in` | The **interaction role** |
| `/division rsvp-channel` and `/division attendance-channel` | The **interaction role** |
| The check-in buttons | No role. A confirmed placement in that division, full-time or reserve — anybody else is told they are not a member of it |
| The pardon button on a penalty review | Whoever runs your penalty reviews |
| Anything under `/images` | See the image guide |

> **The timings can only be set before a season's placements are confirmed.** The three that decide when a check-in happens — the notice, the last reminder and the deadline — are refused from that moment until the season ends, and the timings in force are the ones the bot noted when the season was approved. Changing them mid-season is not possible, and would not move a check-in that is already scheduled even if it were. Decide before you approve.

---

## Step 1 — Turn results & standings on first

```
/module enable results
```

Attendance is refused outright without it, because attendance is worked out from your results: the bot decides who turned up by reading who appears in the session classifications you submit, and it only charges points once a round's post-race penalties are settled. Setting that module up is a job of its own — follow [Setting up the results & standings module](configuring-the-results-module.md) for the order.

That dependency runs both ways. **Turning results & standings off turns attendance off with it.** Because that costs you every division's check-in and attendance channels, the bot stops and tells you before it does anything: you get a warning naming what the cascade will take, and nothing is switched off until you confirm it. The reply that follows names both modules. If attendance was already off there is nothing to warn about, and results goes off straight away.

---

## Step 2 — Switch attendance on

```
/module enable attendance
```

It is refused once a season's placements are confirmed, so this belongs alongside your other setup — with no season, or before you confirm placements. The reply is immediate but nothing happens yet: the bot needs channels to post in and an approved season to work through, which are the next steps.

Switching it on gives you a starting configuration you can leave alone if it suits you — a call five days out, a reminder a day out, check-in closing two hours before the race, one point for each of the three ways of missing it, and no automatic sanctions at all. Step 4 onwards is about changing those.

> **Turning attendance off forgets your channels.** The timings, the penalties and the thresholds all survive being switched off and come back as you left them. The per-division check-in and attendance channels do not — they are deleted, and you will be setting all of them again. That includes the case where the module goes off because you turned results & standings off — which, if a season is running, also deletes that season's results; see [Setting up the results & standings module](configuring-the-results-module.md).

> **Turning it off mid-season stops check-in for the rest of that season, and you cannot undo it.** Every call, reminder and deadline still to come stops at once — no more check-ins posted, no reserves distributed into seats, no attendance points charged. A call already posted stays in the channel, but its buttons stop recording answers. Because the module cannot be switched on once placements are confirmed, you will not get check-in back before the season ends. Turn it off mid-season only if you mean to run the rest of that season without it.

---

## Step 3 — Give every division two channels

```
/division rsvp-channel  name: Division One  channel: #div1-check-in
/division attendance-channel  name: Division One  channel: #div1-attendance
```

Both are set **per division**, so a league with three divisions sets six channels. The first carries the check-in calls, the reminders and the reserve distribution result; the second carries the attendance sheet and nothing else. They can be the same channel if you want them to be, but keeping them apart is easier to read — one is a conversation before the race, the other a table that is replaced after it.

**This is the thing that blocks a season.** While attendance is on, a season cannot be approved until every division has both, and the bot names each division that is missing one along with the command that fixes it.

The check-in call **pings the division's role**, the one set when the division was created. That is the role your drivers need if they want to be told a call has gone out.

> **The bot must be able to post in both channels, and it checks at the time you set them.** If it cannot, the command is refused there and then rather than failing silently three days before a race.

> **A division copied from another does not inherit either channel.** The copy starts with none and nothing warns you at the time — it surfaces later as a season that will not approve. Set them explicitly.

> **Who can see the check-in channel is yours to set, not the bot's.** The bot posts to the channel you give it and never touches its permissions, so restrict it to the division's role yourself if you would rather only that division's drivers could press its buttons. Nothing breaks if you do not: anybody without a confirmed placement in the division is told they are not a member of it and nothing is recorded. Restricting the channel just saves them finding that out.

---

## Step 4 — Decide when the check-in call goes out

Three settings, and between them they lay out the whole week before a race.

| Command | What it sets | Starts as |
|---|---|---|
| `/attendance config rsvp-notice` | Days before the race that the call is posted | 5 days |
| `/attendance config rsvp-last-notice` | Hours before the race that the silent drivers are chased | 24 hours |
| `/attendance config rsvp-deadline` | Hours before the race that check-in closes | 2 hours |

They have to stay in that order — the call first, the reminder after it, the deadline last — and the bot works it out in hours, so a notice of 1 day and a reminder of 24 hours is refused for landing at the same moment. When it refuses, it shows you both values in hours so you can see why. The notice must be at least a day.

**Two of the three can be switched off by setting them to `0`.** A last reminder of `0` means no chasing message at all. A deadline of `0` means check-in stays open right up to the moment the race is scheduled to start. The notice itself cannot be switched off; there is no check-in without a call.

> **With the reminder off, the ordering is no longer enforced.** The bot only compares the reminder against the deadline when the reminder is non-zero, so a reminder of `0` leaves the deadline unchecked against the notice — a notice of 5 days and a deadline of 200 hours is accepted, and closes check-in before it opens. Keep the deadline the smaller number yourself.

**Do this before the season is approved.** The bot reads all three at approval and schedules every round of the season against them there and then. Afterwards the commands are refused, and there is no way to shift a running season's check-ins.

> **A season too close to its first race cannot be approved at all.** Review on the Thursday with a 5-day notice and round 1's call was due two days ago — so the review names the round and the window and offers you no Approve button, and the approval refuses on the same finding if a round crosses a window while the review is standing. Nothing is committed either way. Approve earlier, shorten the notice, or move the round with `/round amend`. Which of those is right is your decision, and it is why the bot will not pick one: the alternative is a round whose call never happens, whose reminder never happens, and which — because the call is what opens the attendance records — counts nothing against anybody and reads afterwards as perfect attendance for the whole division.
>
> This is checked under test mode too, so a test season needs its rounds set beyond every one of these three timings.

To see what is currently set, run `/attendance config show`, which answers privately with the timings, the penalties and the thresholds in one message. `/season placements-review` shows the same block alongside everything else.

---

## Step 5 — Decide what missing a race costs

Three penalties, all starting at 1 point, all settable at any time — these are not blocked by a running season.

| Command | Charged when |
|---|---|
| `/attendance config no-rsvp-penalty` | A driver never touched the buttons at all |
| `/attendance config absent-penalty` | A driver who did not accept — no answer, tentative or declined — is not in the results |
| `/attendance config no-show-penalty` | A driver who accepted is not in the results |

Set a penalty to `0` to stop charging for that case entirely.

**How a round adds up**, for a full-time driver:

| They answered | They raced | They are charged |
|---|---|---|
| Accepted, tentative or declined | Yes | Nothing |
| Never answered | Yes | The no-RSVP penalty |
| Never answered | No | The no-RSVP penalty **plus** the absent penalty |
| Tentative or declined | No | The absent penalty |
| Accepted | No | The no-show penalty |

The only line that stacks two penalties is the driver who ignored the call and then did not show, and the one your drivers will argue about is the driver who accepted and then did not show — which is why the no-show penalty is its own setting rather than sharing the absent penalty. A reserve the bot seated for the round is only ever charged that last one; a reserve it did not seat is charged nothing.

---

## Step 6 — Decide whether the bot acts on its own

```
/attendance config autoreserve  points: 5
/attendance config autosack  points: 8
```

Both are **off to begin with**, and both work on a driver's running total **in one division** — points are counted per division, not across the season — checked every time a round's points are charged. Reach the number and the bot acts immediately.

| Command | What the bot does when a driver reaches the number |
|---|---|
| `/attendance config autoreserve` | Takes them out of their team and puts them in that division's Reserve team |
| `/attendance config autosack` | Removes them from every seat they hold, in **every** division, and takes their driver role away |

Set either to `0` to switch it off.

> **You can only have one of them.** Setting auto-reserve while auto-sack is active is refused, and the other way round. If you want to swap, set the one you have to `0` first — the refusal tells you which command to run.

Either action is announced in the division's verdicts channel, the same place your penalty decisions go, so your league sees why a driver moved. The lineup post is redrawn to match, and the sheet for that same round is reposted straight away with the driver still listed and marked as having reached the limit — a sheet lists everyone who held a seat in the division this season, not only those who still do. An auto-sack also reposts the latest sheet of every **other** division the driver sat in, since they lose those seats too. Where the image module's `Verdict banner` switch is on, these are headed like any other verdict. If the sanction came out of approving a penalty review, it falls under that approval's banner alongside the penalties; if it fired on its own — a clean round, or a pardon that made the bot re-check attendance — it gets a banner of its own.

**Auto-reserve needs somewhere to put them.** If a division has no Reserve team, the sanction cannot be applied, and you are told so — see [When a sanction does not apply](#when-a-sanction-does-not-apply). A driver already in the Reserve team is left alone.

> **This is the one part of the module that changes your grid without being asked.** Try it in test mode before a real season depends on it, and pick numbers you would be comfortable defending — the bot does not ask twice and there is no undo. An auto-reserved driver can be moved back with `/driver move`; an auto-sacked one is back at Not Signed Up, and has to sign up again in a later window and be placed from it.

---

## Step 7 — Decide between text and pictures

Out of the box, the check-in call is an embed and the attendance sheet is a list of names. The image module turns each into a graphic, separately:

```
/module enable images
/images config toggle aspect:Attendance sheet
/images config toggle aspect:Check-in call
```

`attendance` **replaces** the sheet's list with a drawn table — flags, team badges, and a column per round showing what each round cost. `rsvp` **adds** a graphic to the check-in call naming the round, its sessions, its date and the moment check-in closes; everything else about the call is unchanged, including the roster and the three buttons. The picture leads with the **grand prix** and puts the **circuit** on the line beneath it, and shows the country as a flag rather than spelling it out. A mystery round reads "Mystery Grand Prix" over "Mystery".

> **The drawn sheet warns before the limit does.** If you set a threshold in step 6, the picture puts a wash behind the total of anyone near it: **amber** for a driver **within two points** of the limit, **red** once they have **reached** it. The two are the same weight and are told apart by colour, so the warning stands out rather than looking like a weaker version of the sanction. It is a warning a manager can read at a glance, a round or two before the bot acts. A driver on zero is never marked, however low you set the limit, and if you set no threshold nothing is marked at all. The plain text list carries no such warning — this is one of the things the picture adds.
>
> The sheet also shows the limit itself, on a single plate naming whichever of the two you set: `RESERVE AT 5`, or `SACKED AT 8`. One plate, because you can only have one threshold.

Follow [Setting up the image module](configuring-the-image-module.md) for the order — the drawing files, the flags and the badges.

> **A picture never delays or changes a sanction.** Auto-reserve and auto-sack are enforced and announced exactly as they would be with the images module switched off, and a sheet that cannot be drawn falls back to the list with the reason in the log channel.

> **The sheet's drawing file has to be big enough for your division.** With `attendance` on, assigning a driver that would push a division past the rows your file declares is **refused** and the driver is not seated. `/season placements-review` also warns where your sheet has fewer round columns than your longest calendar, or your check-in graphic names fewer sessions than a sprint weekend runs — those are warnings and do not block approval.

---

## Step 8 — Try it without waiting

You are not going to wait five days to find out whether any of this works, and you certainly cannot round up twenty people to press buttons.

```
/test-mode toggle
/test-mode advance
/test-mode rsvp set-status  division: Division One
```

`advance` fires the next thing due — the call, then the reminder, then the deadline — straight away, and posts each to the real channels so you see exactly what your drivers will see. `set-status` then opens a box where you can set every test driver's answer in one pass, which is the only practical way to drive a check-in to a known state; it needs a call already posted, so run it after the first `advance`. Keep advancing to fire the reminder and close check-in, and watch the reserves get distributed.

See [Test mode](test-mode.md) for the whole picture, including the synthetic drivers you will need first.

> **Test mode is chosen for a season, in its configuration.** `/test-mode toggle` works only while a season is in configuration, and is refused while any real driver is signed up, unassigned or assigned — which, once a season has ended, nobody is. A season confirmed in test mode never opens a signup window, and while test mode is on no real driver may sign up or be placed. It stays on until that season is completed, cancelled or aborted, which switches it off and deletes the fake drivers — so test in a season of its own, and abort it with `/season abort` if you would rather not race it out. See [Setting up the bot for your league](configuring-the-core-bot.md) for the season's stages.

> **Leaving test mode deletes every fake driver on the server**, across all divisions, without asking — when you toggle it off in configuration, or when the test season ends. Build your test roster expecting to lose it.

---

## What your drivers see

**The call**, at the notice moment. A ping for the division role, then an embed titled with the season, the round and the circuit — or *Mystery*, for a mystery round. It gives the date as a live Discord timestamp, the location and the kind of weekend, then lists every driver grouped by team with the Reserve team last, a marker beside each name: `()` for no answer yet, `(✅)` accepted, `(❓)` tentative, `(❌)` declined. Under it, three buttons: **✅ Accept**, **❓ Tentative**, **❌ Decline**.

Pressing one updates the roster in the message itself, so the division can see at a glance who is still missing. The driver gets a small private confirmation nobody else sees. Pressing the same button twice is harmless, and changing your mind is fine until the deadline — except for a reserve who has not accepted, who can still change right up to the moment the race is scheduled to start. A reserve who *has* accepted is locked at the deadline like anybody else.

**The reminder**, at the last-notice moment. If anyone full-time is still silent, the bot pings exactly those people and says how long is left. If everybody has answered, it says so instead and asks them to check nothing has changed.

**The close**, at the deadline. The buttons disappear from the call, and the bot posts what the reserves did — each one placed into a named team, or told they are on standby because there was no seat for them. If nobody in the Reserve team accepted, or every seat was already filled, it posts a line saying no reserves were placed and all seats are filled. That single message covers both cases, so it appears even when seats were in fact empty and nobody volunteered.

> **A driver placed mid-season is not called until their placement is confirmed.** The drivers of a signup window opened during the season stand outside the championship until `/season placements-review` confirms them: no check-in call, no attendance points, and no reserve seat. Rounds that run meanwhile run without them. If one of them can see a check-in channel and presses a button anyway, they are told they are not a member of the division and nothing is recorded — the same answer anyone else without a seat in it gets, including you if you do not race.

**A driver who joins a division while a call is standing can answer it.** Confirm a new driver, or move somebody in with `/driver move`, and the call already sitting in the channel picks them up: it is redrawn from the current roster on every press, so they appear on it with an empty bracket and their answer counts like everybody else's. Until they answer they have no attendance record for that round at all, which is worth knowing if you are reading the call to work out who is still silent — a name that has only just arrived has not been ignoring you.

The ordinary locks still decide whether they are in time. Move a full-time driver in after the deadline has gone by and they are told it has passed, exactly as a driver who sat on their hands would be. A reserve who has not accepted can still step in right up to the scheduled start — but the reserves were distributed into their seats at the deadline, so a late yes from one arrives after the grid was settled and will not place them in a team. It is still worth recording: it tells you who volunteered if somebody no-shows.

**The sheet**, after a round's post-race penalties are approved. It goes to the attendance channel, sorted worst first, one line per driver with their total. If you have set a threshold, the footer says what happens at it — and with the images module on, the drawn sheet marks the drivers who are closing on it. Each new sheet replaces the last, so the channel always holds exactly one.

---

## How a round is scored

**Turning up is decided from your results, not from the buttons.** Once you submit a round's results, anyone appearing in **any** session of that round is marked present — qualifying counts, and so does a race a driver was disqualified from or never got away in. Only somebody absent from the lot is marked missing.

**Points are only charged when the round is finished with.** Not at provisional results, but when post-race penalties are approved. That is deliberate: it gives you a window to correct a classification that accidentally left somebody out before anyone is charged for it, and before a sanction can fire on a mistake.

**Amending the results afterwards puts it right.** Re-run through `/round results amend` and the bot recalculates that round's attendance, every later round's totals, reposts the sheet and re-checks the thresholds. Two things to expect: the round has to be **FINAL** before `/round results amend` will touch it, so a round still sitting at post-race penalties is refused; and the recalculation happens when you approve the **last** of the amendment's three steps, not when you paste the corrected classification.

> **The amendment is also where you change a pardon.** Pardons you granted are carried into the amendment's report step and kept as they are unless you say otherwise — and that step is the only place to add one, edit one or take one back after a round has gone final. See [the results guide](configuring-the-results-module.md#a-classification-that-was-wrong) for the three steps in order.

> **The sheet you get back is the division's current one, not the amended round's.** Each round's total is what the driver stood on at that round, so after correcting round 3 of ten the totals you want to see are round 10's. That is the sheet posted, and those are the numbers the auto-reserve and auto-sack limits are checked against.

> **An amendment can clear a charge, but it will not create one.** A driver the bot has already recorded as present stays present, even where the corrected classification no longer lists them — they were given no chance to explain themselves, so the record errs in their favour. Adding somebody who was left out clears what they were charged; taking somebody out who should never have been there does not charge them. Where a recorded attendance genuinely has to be taken back, `/attendance sync` rebuilds the round from its results outright.

> **Amending the round itself is a different thing, and the check-in comes with it.** Changing a round's date, circuit or format with `/round amend` re-arms the check-in against the round as it now stands. What happens to a call that has already gone out depends on how far the round moved. Move it well out and the call standing is taken down and a fresh one posted at the new time. Move it a little, or change only the circuit, and the call is posted again straight away carrying what changed — with every answer already given kept, so your drivers do not have to answer twice. A driver who joined the division since is asked afresh; one who has left drops off it.
>
> **Past the deadline, though, the check-in is settled.** If the round's check-in deadline has already gone by, amending the round leaves the check-in exactly as it is: nothing is reposted, nothing taken down, and no answer can be changed. The reserves have been distributed against those answers and reopening the check-in would unsettle a grid already told who is racing. Moving the round far enough out puts it back in play.
>
> **And you cannot move a round inside its own deadline.** On the default two-hour deadline, moving a round to less than two hours away is refused outright, because the check-in would open and close in the same instant. Move it further out, or shorten the deadline first.

**Reserves are scored separately.** A reserve the bot seated for the round is charged only if they accepted and then did not appear. A reserve who was never seated is charged nothing, whatever they clicked.

**Cancelling a round tells the division here.** When you cancel a round, a division or the season, the check-in channel gets a notice saying so, mentioning the division role as the check-in call does — it is the one notification the bot sends about a cancellation, the forecast and results channels getting only silent notes. The bot announces nothing beyond that, so the rest of telling your drivers is yours to do.

**The check-in call for a cancelled round comes down.** Once the notice is up, the round's check-in call is deleted, along with its reminder and its reserve-distribution message, so nobody goes on answering a round that is off. If the bot is not allowed to delete one, your reply to the cancel command says so and names it, and it is yours to delete by hand; until you do, its buttons refuse every answer. The answers already given are not thrown away: they stay in the bot's records, and the log channel entry for the cancellation lists them — who accepted, was unsure, declined or never answered, and, if the reserves had already been placed, who was sent to which team and who was on standby.

**A cancelled round charges nobody**, and neither does a round whose check-in call never got posted — no call means no records were ever opened, and the round quietly counts as perfect attendance for everyone. The log channel reports the failure loudly when it happens, and it is worth acting on.

**The sheet is also posted at each end of the season.** Approving the season posts an **opening sheet** — every seated driver on zero, read from the seats rather than from an attendance record that does not exist yet, ordered alphabetically by team and then by driver. `/season complete` posts a **final sheet**, holding the record at that division's last round with results. Both go to the same attendance channel, as drawings where you turned pictures on and as the ordinary list where you did not. Neither is about a round, so a cancelled round does not stop either.

> **The final sheet is the one thing that does not replace what came before it.** Every other posting deletes the previous sheet so exactly one stands in the channel; the final one is posted **beside** the last round's and both stay. It is the season's last word and nothing should be able to delete it. The opening sheet behaves normally — the first round's sheet replaces it in the usual way, so you are never left with a stale opening sheet beside a live one.

---

## Pardoning a driver

Sometimes there is a reason, and the bot has no way of knowing it. Pardons are granted from the **🏳️ Attendance Pardon** button on the penalty review — the same review where you settle the round's on-track penalties — and only there.

It asks for three things: the driver's Discord user ID, which of the three charges you are lifting (`NO_RSVP`, `ABSENT` or `NO_SHOW`), and a justification. **The justification is for your records only.** It is never shown to the league, which is the point — a driver's reason for missing a race is their own business.

Each pardon lifts one charge, so a driver who never answered *and* did not show can be pardoned once or twice depending on how generous you feel. The bot checks the pardon matches what actually happened and refuses one that does not — you cannot pardon a no-show for someone who declined.

Pardons are staged with the round's penalties and listed alongside them for review before you commit. **Once the post-race penalties are approved, that round can no longer be pardoned** — the charge stands until the round's results are amended.

---

## When a sanction does not apply

The bot tries every driver over the threshold, and one driver's sanction failing never stops the next. Nothing that did apply is undone. What did not apply is never kept quiet:

1. **Read what you are told.** The reply to your penalty-review or amendment approval lists each driver whose sanction did not apply and why, and the log channel carries the same list as an `ATTENDANCE_SANCTIONS | Incomplete` entry. Both end with the exact `/attendance sync` command to run.
2. **Put the cause right.** Usually it is a division with no Reserve team, or the bot lacking the permission to change a driver's roles. A line saying *applied, but not announced* means the sanction did take effect and only its announcement failed — there is nothing left to apply for that driver, and **the sync will not announce it either**, because a driver already sacked or already in Reserve is no longer a candidate. Post that one in the verdicts channel yourself.
3. **Run `/attendance sync`** with the division and round it names. It recalculates that round and every later one whose penalties are approved, reposts the latest sheet, and applies whatever is still owed, with its announcement in the verdicts channel as usual. The reply tells you what it applied and anything still outstanding.

Running it twice does no harm: a driver already sacked or already in the Reserve team is not sanctioned again. It is available only while the season is ongoing, and is refused, with nothing changed, if a channel it would post to cannot be reached. You can also use it to put a division's attendance right after anything else went wrong in a round's scoring.

## When the attendance itself was not recorded

Different from the above, and more serious. Approving a penalty review records who attended the round and awards the attendance points. Both write to the league's record, and the thresholds above are read from it — so a failure here can see a driver sanctioned later on a total that was never right.

You get an `ATTENDANCE_RECORD | Incomplete` entry in the log channel and a line in your reply to the approval, ending with the `/attendance sync` to run. It is deliberately a separate entry from `ATTENDANCE_SANCTIONS | Incomplete`: that one means a sanction did not apply while the record itself is sound, and this one means the record is wrong.

Run the sync the same day. Until you do, that round's attendance is wrong and every total after it is short.

**The sanctions are held back when this happens**, and the entry says so. The thresholds are read from the totals those two steps write, so a driver could otherwise be sacked on a number the bot already knows is wrong — and wrong in either direction, since a driver who raced may have been scored absent. Running `/attendance sync` repairs the record and then applies whatever is genuinely owed, in one go. A sheet or an announcement that failed to post does *not* hold the sanctions back: the record is sound in that case, only the picture of it is missing.

---

## When the check-in call never went out

The most damaging of the three, and the quietest. A round whose check-in call never posted opens no attendance records at all — so nobody is asked whether they are racing, nobody can be marked absent, and the round is scored afterwards as **perfect attendance for the whole division**. Nothing about the finished round looks wrong.

You find out from the log channel, which gets an `ATTENDANCE | check-in call | NOT POSTED` entry at the moment it fails, naming the division, the round and the reason.

**Find the cause before you post anything.** The entry says what went wrong, and it is nearly always one of two things: the division's check-in channel has been deleted, renamed or had the bot's permissions changed, or Discord refused the message. Posting the call again before you have fixed that just fails a second time.

1. **Read the reason** in the log entry.
2. **Put the cause right** — usually restoring the channel in `/division rsvp-channel`, or giving the bot permission to post and embed links there.
3. **Post the call** with `/attendance post-check-in`, naming the division and the round. The log entry gives you the command with both already filled in, so you can copy it straight out.
4. **Check the reply.** It tells you whether a call is now standing, not merely that the command was accepted — so a second failure is not mistaken for a success.

The call goes out exactly as the scheduled one would have, opens the round's attendance records, and the deadline and reminder already scheduled for the round still apply.

> **It will refuse you in three cases, all deliberate.** If a check-in call is **already standing**, use [`/round amend`](../../README.md#round-amend--amend-a-round-in-the-active-season) instead — that takes the old call down and posts a new one carrying every answer already given across, where a second call would split your division's answers between two messages. If the call is **not yet due**, the scheduled one is still coming and posting now would override the notice period you set. And if the **deadline has passed**, a call posted now would arrive with its buttons locked, so nobody could answer it.

**If you are reaching for this often, something is broken that nobody has fixed.** It is a repair, not part of running a season — a league that needs it every few rounds has a channel or permission problem worth chasing down properly.

---

## What you cannot change

Worth knowing so you do not go looking for the setting.

| What | Why not |
|---|---|
| The wording, layout and buttons of the check-in call | Fixed. The graphic beside it is yours to draw once the image module is on |
| Which drivers get asked | Everybody seated in the division, including the Reserve team. There is no opting out and no per-driver exemption — a pardon after the fact is the mechanism |
| Whether a mystery round gets a call | It always does. Unlike forecasts, check-in does not care that the circuit is secret |
| How reserves are ordered for a seat | By when they accepted, earliest first. Changing your answer and changing it back puts you at the back of the queue |
| Which team a reserve lands in | Worked out from who is missing. A team with nobody at all comes first, then one whose driver declined, then one whose driver never answered, then one with an empty seat, and last a team whose only gap is a tentative driver. Every team gets one before any team gets two, and where two are equal the team further down the constructors' table is served first |
| Marking somebody present by hand | There is no command. Presence comes from the results; correct the results with `/round results amend` |
| Having both auto-reserve and auto-sack | Mutually exclusive by design |
| Where the sanction announcements go | The division's verdicts channel, alongside your penalty decisions |

---

## Checklist before a season

Worth running through before the season is approved.

- [ ] `/module enable results` has been run, and then `/module enable attendance`
- [ ] Every division has a check-in channel **and** an attendance channel, including any you created by copying another
- [ ] Every division has the role you want pinged
- [ ] Every division has drivers seated in its Reserve team, if you want reserves distributed
- [ ] The three timings are what you want, and `/attendance config show` agrees
- [ ] The first round is far enough away that its check-in has not already been missed — the review withholds the Approve button if not, so this one is worth settling before you get there
- [ ] The two working penalties are set — remembering the third is stuck at 1
- [ ] Auto-reserve or auto-sack is set to a number you would defend, or deliberately left off
- [ ] If either is on, every division has a Reserve team and a verdicts channel
- [ ] If you want pictures: the image module is on, the two aspects are toggled, and the sheet's drawing file has rows enough for your biggest division
- [ ] You have watched one full round go by with `/test-mode advance` and `/test-mode rsvp set-status`

---

## If something looks wrong

| What you see | Usually means |
|---|---|
| No check-in call for a division | No check-in channel set for it, or the module is off |
| A division's calendar names a round and a check-in window, with no Approve button | That round is already inside the window — its call fell due before you ran the review. Only the latest such round is named; put it right and the earlier ones go with it. Move the round, or shorten the notice, and review again |
| A call that never appeared, and a loud report in the log channel | The bot could not post it. No records were opened, so the round is a free pass for the whole division. Fix the cause the entry names, then run `/attendance post-check-in` |
| No check-in for a round you moved or re-tracked | The round was moved past its own check-in deadline, so the check-in stayed closed. Check the round's time against your deadline setting |
| A round where nobody was charged anything | The round was cancelled, or its call never posted — check the log channel, which reports a failed call loudly. A call still inside its deadline can be posted with `/attendance post-check-in` |
| `/module enable attendance` refused | Results & standings is off, or a season is already running |
| Attendance switched off by itself, and the channels gone | Results & standings was turned off and the cascade warning confirmed, which takes attendance with it and deletes every division's channels |
| Check-in stopped mid-season and will not come back | Attendance was switched off, directly or by turning results & standings off. It stays off until the season ends |
| `/attendance config` on a timing refused | Either the season's placements are confirmed, or the value would put the three out of order. The reply says which |
| Auto-reserve or auto-sack refused | The other one is set. Set it to `0` first |
| A driver over the threshold who was not sanctioned | Either they are in the Reserve team already, which is intended, or the sanction failed — the log channel says which driver and why. Put it right and run `/attendance sync` |
| Points charged later than you expected | They are charged when post-race penalties are approved, never at provisional results |
| A driver charged for a round they raced | They are in no session's results. Correct the classification with `/round results amend` and the round is recalculated |
| Reserves not distributed | Nobody in the Reserve team accepted, or there was no vacancy — an accepted seat is never a vacancy, however slow the driver was to answer |
| A reserve told they are on standby | Every team that needed one already had one. That is the intended outcome, not a failure |
| Text where you expected a picture | The sheet worked and the drawing did not. The log channel names the reason |
| A driver assignment refused with the images module on | The sheet's drawing file has fewer rows than the division now needs. Enlarge it |
