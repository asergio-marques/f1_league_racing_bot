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
| `/module enable results` and `/module enable attendance` | **Administrator** |
| `/attendance config` — all of them | **Manage Server** |
| `/division rsvp-channel` and `/division attendance-channel` | **Manage Server** |
| The check-in buttons | Nothing. Any driver seated in that division can press them |
| The pardon button on a penalty review | Whoever runs your penalty reviews |
| Anything under `/images` | See the image guide |

> **The timings can only be set before a season is running.** The three that decide when a check-in happens — the notice, the last reminder and the deadline — are refused while a season is active, and the timings in force are the ones the bot noted when the season was approved. Changing them mid-season is not possible, and would not move a check-in that is already scheduled even if it were. Decide before you approve.

---

## Step 1 — Turn results & standings on first

```
/module enable results
```

Attendance is refused outright without it, because attendance is worked out from your results: the bot decides who turned up by reading who appears in the session classifications you submit, and it only charges points once a round's post-race penalties are settled. Setting that module up is a job of its own — follow [Setting up the results & standings module](configuring-the-results-module.md) for the order.

That dependency runs both ways. **Turning results & standings off turns attendance off with it**, and it does so quietly — you get the one reply about results, and nothing tells you attendance went too.

---

## Step 2 — Switch attendance on

```
/module enable attendance
```

It can only be done while no season is active, so this belongs alongside your other setup, before approval. The reply is immediate but nothing happens yet: the bot needs channels to post in and an approved season to work through, which are the next steps.

Switching it on gives you a starting configuration you can leave alone if it suits you — a call five days out, a reminder a day out, check-in closing two hours before the race, one point for each of the three ways of missing it, and no automatic sanctions at all. Step 4 onwards is about changing those.

> **Turning attendance off forgets your channels.** The timings, the penalties and the thresholds all survive being switched off and come back as you left them. The per-division check-in and attendance channels do not — they are deleted, and you will be setting all of them again. That includes the case where the module goes off because you turned results & standings off.

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

> **Approving a season close to its first race silently costs you that round's check-in.** Anything whose moment has already passed at the point of approval is simply not scheduled — approve on the Thursday with a 5-day notice and round 1's call never happens, its reminder never happens, and because the call is what opens the attendance records, that round counts nothing against anybody. Nothing warns you. Approve early, or shorten the notice for a season that starts soon.

To see what is currently set, run `/attendance config show`, which answers privately with the timings, the penalties and the thresholds in one message. `/season review` shows the same block alongside everything else.

---

## Step 5 — Decide what missing a race costs

Three penalties, all starting at 1 point, all settable at any time — these are not blocked by a running season.

| Command | Charged when |
|---|---|
| `/attendance config no-rsvp-penalty` | A driver never touched the buttons at all |
| `/attendance config absent-penalty` | A driver who did not accept — no answer, tentative or declined — is not in the results |
| `/attendance config rsvp-absent-penalty` | A driver who accepted is not in the results |

Set a penalty to `0` to stop charging for that case entirely.

**How a round adds up**, for a full-time driver:

| They answered | They raced | They are charged |
|---|---|---|
| Accepted, tentative or declined | Yes | Nothing |
| Never answered | Yes | The no-RSVP penalty |
| Never answered | No | The no-RSVP penalty **plus** the absent penalty |
| Tentative or declined | No | The absent penalty |
| Accepted | No | The accepted-and-absent penalty |

The only line that stacks two penalties is the driver who ignored the call and then did not show, and the one your drivers will argue about is the accepted-and-absent driver — which is why it is its own setting rather than sharing the absent penalty. A reserve the bot seated for the round is only ever charged that last one; a reserve it did not seat is charged nothing.

> **`/attendance config rsvp-absent-penalty` does not work.** Run it and the bot fails to respond — the setting is never written, whatever value you pass. That penalty is therefore stuck at 1 point for everybody, and `/attendance config show` will keep reporting 1 no matter what you do. The other two penalties are unaffected and set normally. This is a defect in the bot, recorded in [known issues](../wip-specs/known_issues.md), not something you have configured wrongly.

---

## Step 6 — Decide whether the bot acts on its own

```
/attendance config autoreserve  points: 5
/attendance config autosack  points: 8
```

Both are **off to begin with**, and both work on a driver's running total across the season, checked every time a round's points are charged. Reach the number and the bot acts immediately.

| Command | What the bot does when a driver reaches the number |
|---|---|
| `/attendance config autoreserve` | Takes them out of their team and puts them in that division's Reserve team |
| `/attendance config autosack` | Removes them from every seat they hold, in **every** division, and takes their driver role away |

Set either to `0` to switch it off.

> **You can only have one of them.** Setting auto-reserve while auto-sack is active is refused, and the other way round. If you want to swap, set the one you have to `0` first — the refusal tells you which command to run.

Either action is announced in the division's verdicts channel, the same place your penalty decisions go, so your league sees why a driver moved. The lineup post is redrawn to match, and the sheet for that same round is reposted straight away with the driver marked as having reached the limit.

**Auto-reserve needs somewhere to put them.** If a division has no Reserve team, the sanction is skipped **silently** — nothing is posted to the verdicts channel, nothing to the log channel, nothing anywhere you can see; it reaches only the bot's own log file on the host. A driver already in the Reserve team is left alone, equally silently.

> **This is the one part of the module that changes your grid without being asked.** Try it in test mode before a real season depends on it, and pick numbers you would be comfortable defending — the bot does not ask twice and there is no undo beyond re-seating the driver by hand.

---

## Step 7 — Decide between text and pictures

Out of the box, the check-in call is an embed and the attendance sheet is a list of names. The image module turns each into a graphic, separately:

```
/module enable images
/images config toggle aspect:Attendance sheet
/images config toggle aspect:Check-in call
```

`attendance` **replaces** the sheet's list with a drawn table — flags, team badges, and a column per round showing what each round cost. `rsvp` **adds** a graphic to the check-in call naming the round, its sessions, its date and the moment check-in closes; everything else about the call is unchanged, including the roster and the three buttons.

> **The drawn sheet warns before the limit does.** If you set a threshold in step 6, the picture puts a wash behind the total of anyone near it: **amber** for a driver **within two points** of the limit, **red** once they have **reached** it. The two are the same weight and are told apart by colour, so the warning stands out rather than looking like a weaker version of the sanction. It is a warning a manager can read at a glance, a round or two before the bot acts. A driver on zero is never marked, however low you set the limit, and if you set no threshold nothing is marked at all. The plain text list carries no such warning — this is one of the things the picture adds.
>
> The sheet also shows the limit itself, on a single plate naming whichever of the two you set: `RESERVE AT 5`, or `SACKED AT 8`. One plate, because you can only have one threshold.

Follow [Setting up the image module](configuring-the-image-module.md) for the order — the drawing files, the flags and the badges.

> **A picture never delays or changes a sanction.** Auto-reserve and auto-sack are enforced and announced exactly as they would be with the images module switched off, and a sheet that cannot be drawn falls back to the list with the reason in the log channel.

> **The sheet's drawing file has to be big enough for your division.** With `attendance` on, assigning a driver that would push a division past the rows your file declares is **refused** and the driver is not seated. `/season review` also warns where your sheet has fewer round columns than your longest calendar, or your check-in graphic names fewer sessions than a sprint weekend runs — those are warnings and do not block approval.

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

> **Turning test mode off deletes every fake driver on the server**, across all divisions, without asking. Build your test roster expecting to lose it.

---

## What your drivers see

**The call**, at the notice moment. A ping for the division role, then an embed titled with the season, the round and the circuit — or *Mystery*, for a mystery round. It gives the date as a live Discord timestamp, the location and the kind of weekend, then lists every driver grouped by team with the Reserve team last, a marker beside each name: `()` for no answer yet, `(✅)` accepted, `(❓)` tentative, `(❌)` declined. Under it, three buttons: **✅ Accept**, **❓ Tentative**, **❌ Decline**.

Pressing one updates the roster in the message itself, so the division can see at a glance who is still missing. The driver gets a small private confirmation nobody else sees. Pressing the same button twice is harmless, and changing your mind is fine until the deadline — except for a reserve who has not accepted, who can still change right up to the moment the race is scheduled to start. A reserve who *has* accepted is locked at the deadline like anybody else.

**The reminder**, at the last-notice moment. If anyone full-time is still silent, the bot pings exactly those people and says how long is left. If everybody has answered, it says so instead and asks them to check nothing has changed.

**The close**, at the deadline. The buttons disappear from the call, and the bot posts what the reserves did — each one placed into a named team, or told they are on standby because there was no seat for them. If nobody in the Reserve team accepted, or every seat was already filled, it posts a line saying no reserves were placed and all seats are filled. That single message covers both cases, so it appears even when seats were in fact empty and nobody volunteered.

**The sheet**, after a round's post-race penalties are approved. It goes to the attendance channel, sorted worst first, one line per driver with their total. If you have set a threshold, the footer says what happens at it — and with the images module on, the drawn sheet marks the drivers who are closing on it. Each new sheet replaces the last, so the channel always holds exactly one.

---

## How a round is scored

**Turning up is decided from your results, not from the buttons.** Once you submit a round's results, anyone appearing in **any** session of that round is marked present — qualifying counts, and so does a race a driver was disqualified from or never got away in. Only somebody absent from the lot is marked missing.

**Points are only charged when the round is finished with.** Not at provisional results, but when post-race penalties are approved. That is deliberate: it gives you a window to correct a classification that accidentally left somebody out before anyone is charged for it, and before a sanction can fire on a mistake.

**Amending the results afterwards puts it right.** Re-run through `/round results amend` and the bot recalculates that round's attendance, every later round's totals, reposts the sheet and re-checks the thresholds. Pardons you granted are kept. Two things to expect: the round has to be **FINAL** before `/round results amend` will touch it, so a round still sitting at post-race penalties is refused; and the recalculation happens when you *approve* the amendment, not when you submit it.

> **Amending the round itself is a different thing, and it costs you the check-in.** Changing a round's date, circuit or format with `/round amend` cancels everything the bot had scheduled for it and only puts the forecasts back. The check-in call, the reminder and the deadline are gone for good, so that round never asks anybody anything and never charges anybody. Nothing warns you. If you must move a round, expect to treat it as an untracked one.

**Reserves are scored separately.** A reserve the bot seated for the round is charged only if they accepted and then did not appear. A reserve who was never seated is charged nothing, whatever they clicked.

**A cancelled round charges nobody**, and neither does a round whose check-in call never got posted — no call means no records were ever opened, and the round quietly counts as perfect attendance for everyone. The log channel reports the failure loudly when it happens, and it is worth acting on.

---

## Pardoning a driver

Sometimes there is a reason, and the bot has no way of knowing it. Pardons are granted from the **🏳️ Attendance Pardon** button on the penalty review — the same review where you settle the round's on-track penalties — and only there.

It asks for three things: the driver's Discord user ID, which of the three charges you are lifting (`NO_RSVP`, `ABSENT` or `NO_SHOW`), and a justification. **The justification is for your records only.** It is never shown to the league, which is the point — a driver's reason for missing a race is their own business.

Each pardon lifts one charge, so a driver who never answered *and* did not show can be pardoned once or twice depending on how generous you feel. The bot checks the pardon matches what actually happened and refuses one that does not — you cannot pardon a no-show for someone who declined.

Pardons are staged with the round's penalties and listed alongside them for review before you commit. **Once the post-race penalties are approved, that round can no longer be pardoned** — the charge stands until the round's results are amended.

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
| Posting a check-in call yourself | There is no command for it. A call the bot skipped or failed to post cannot be reinstated, and that round goes untracked |
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
- [ ] The first round is far enough away that its check-in has not already been missed
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
| No check-in call for the first round only | The season was approved after that round's notice moment had already passed. Nothing warns you, and that round will count nothing against anyone |
| A call that never appeared, and a loud report in the log channel | The bot could not post it. No records were opened, so the round is a free pass for the whole division |
| No check-in for a round you moved or re-tracked | Known: `/round amend` cancels a round's check-in and never puts it back |
| A round where nobody was charged anything | The round was cancelled, its call never posted, or the round was amended |
| `/module enable attendance` refused | Results & standings is off, or a season is already running |
| Attendance switched off by itself, and the channels gone | Results & standings was turned off, which takes attendance with it and deletes every division's channels |
| `/attendance config` on a timing refused | Either a season is running, or the value would put the three out of order. The reply says which |
| `/attendance config rsvp-absent-penalty` fails to respond | Known: the command is broken and that penalty cannot be changed from 1 |
| Auto-reserve or auto-sack refused | The other one is set. Set it to `0` first |
| A driver over the threshold who was not sanctioned | Auto-reserve with no Reserve team in that division, or the driver is in it already. Nothing is posted either way — check the division's Reserve team yourself |
| Points charged later than you expected | They are charged when post-race penalties are approved, never at provisional results |
| A driver charged for a round they raced | They are in no session's results. Correct the classification with `/round results amend` and the round is recalculated |
| Reserves not distributed | Nobody in the Reserve team accepted, or there was no vacancy — an accepted seat is never a vacancy, however slow the driver was to answer |
| A reserve told they are on standby | Every team that needed one already had one. That is the intended outcome, not a failure |
| Text where you expected a picture | The sheet worked and the drawing did not. The log channel names the reason |
| A driver assignment refused with the images module on | The sheet's drawing file has fewer rows than the division now needs. Enlarge it |
