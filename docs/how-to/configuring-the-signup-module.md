# Setting up the signup module

Turn the signup module on and your drivers enrol themselves. You post one button; each driver who presses it gets a private channel, works through a short questionnaire, and lands in a queue for you to approve or turn away. What comes out the other end is a list of approved drivers ranked by lap time, ready to be put into teams.

This guide is the **order to do things in**, from switching the module on to a division lineup posted in its channel. It sends you to the reference for the fine print:

- **[Signup Module Commands](../../README.md#signup-module-commands)** in the main README — every `/signup` command in full.
- **[Driver Commands](../../README.md#driver-commands)** — the commands that place an approved driver into a team.
- **[Team Commands](../../README.md#team-commands)** — the team list the questionnaire offers.
- **[Module Commands](../../README.md#module-commands)** — turning modules on and off.

You do not need to read those first. Start here.

This guide covers the signup module only. Setting the bot up, creating a season, adding divisions, adding rounds and naming a division's lineup channel are a job of their own — follow **[Setting up the bot for your league](configuring-the-core-bot.md)** for those. Where signups depend on one of them, it is named and linked, not explained.

---

## A note on four words

**Wizard** — the private channel a driver gets when they press the button, and the run of questions inside it. One per driver, named after them, deleted 24 hours after their signup ends however it ends.

**Slot** — a day and time you offer as a race slot, so drivers can say which ones they can make. You set the list; they tick the ones that suit. Nothing else in the bot uses them — they exist so you can work out who can race when.

**Seed** — a driver's rank in the approved queue, worked out by adding up the lap times they submitted. Fastest total is seed 1. It is a sorting aid for you, not something the bot acts on.

**Unassigned** — an approved driver who is in no team yet. This is a state on the driver, not on their signup, and it is the whole point of the exercise: signups turn strangers into Unassigned drivers, and placement turns Unassigned drivers into a lineup.

A driver's status lives on the driver, not on their signup form. "Reserve" is an answer they gave to a question, not a status. "Withdrawn", "rejected" and "timed out" all land in the same place — Not Signed Up, as though they had never started.

---

## Before you start

**The bot must already be set up.** You need the interaction channel, the interaction role and the log channel in place. If you have not got that far, follow [Setting up the bot for your league](configuring-the-core-bot.md) first; this guide picks up from there.

**Both privileged intents must be on.** The signup module is the one part of the bot that needs both. Without **Server Members** it cannot grant or revoke a single role; without **Message Content** the wizard never sees a word your drivers type, and every signup stalls on question one. See [Privileged Gateway Intents](../../README.md#privileged-gateway-intents).

**You do not need a season to run signups.** Opening the window, collecting entries and approving drivers all work with no season at all. A season is needed only at the last step, when you start putting drivers into teams.

**Who is allowed to run what.** Every command below also has to be run in the bot's usual command channel by someone with the usual bot role.

| Commands | You need |
|---|---|
| `/module enable signup` | **Administrator** |
| `/signup channel`, `/signup base-role`, `/signup complete-role` | **Administrator** |
| Everything else under `/signup` | **Manage Server** |
| `/driver assign`, `/driver unassign`, `/driver sack` | **Manage Server** |

> **The bot role is required on top of the Discord permission, not instead of it.** An Administrator who does not hold the interaction role is refused every command here, and so is anyone running one outside the interaction channel. Being the server owner does not get you past it.

---

## Step 1 — Switch it on

```
/module enable signup
```

The signup module can be switched on or off whenever you like — unlike weather, it is not tied to whether a season is running.

Nothing is configured yet. The bot creates an empty configuration record and tells you the three commands to run next, which are step 2. The question settings are not written at this point at all — until you change one, the bot simply falls back to its defaults.

Turning the module off force-closes an open window, hands back the permissions it applied to your signup channel, and forgets your channel and your two roles.

> **Turning it off keeps more than it says it does.** The bot reports that all signup configuration has been cleared. Your time slots, your three question settings, and every signup drivers have already submitted all survive. Only the channel and the two roles are actually forgotten. Switch the module back on and you will find your old slots waiting — which is convenient, but not what the message led you to expect.

---

## Step 2 — Give it a channel and two roles

```
/signup channel channel:#signups
/signup base-role role:@League Member
/signup complete-role role:@Driver
```

Three things, all **Administrator**, and the module does nothing at all until it has all three.

**The channel** is where the Sign Up button gets posted. Setting it rewrites the channel's permissions: everyone loses sight of it, the base role can see it but cannot type in it, and holders of the bot role can see and type. That is deliberate — the only thing a driver should be doing in there is pressing the button.

**The base role** is who the signups are for. It decides who can see the channel, and it is the role that gets pinged when you open the window.

**The complete role** is the reward. The bot grants it the moment you approve a driver, so it is the badge that says someone is through the door.

> **Use a channel of its own, and nothing else.** Setting the signup channel replaces every permission override on it, and moving the signup channel elsewhere strips the old one bare on the way out. Any permissions you had set up by hand go with them.

> **The signup channel cannot be your bot command channel.** The bot refuses outright. They are doing opposite jobs — one is for drivers who may not type, the other is for admins who must.

> **Do not use `/signup config channel`.** The old command is broken and fails with an error whatever you pass it. `/signup channel` is the one that works. `/signup config roles` still works and sets both roles at once, but it does not fix up the channel's permissions, so prefer the separate commands.

**These three block a season.** While the signup module is on, `/season approve` refuses until all three are set, and names the ones that are missing. If you are not going to use signups this season, turn the module off rather than leaving it half-configured.

---

## Step 3 — Decide what the wizard asks

Three settings, each a toggle with no parameters. Run the command and it flips.

| Command | What it sets | Starts as |
|---|---|---|
| `/signup nationality` | Whether drivers are asked their nationality | On |
| `/signup time-type` | Whether lap times are called Time Trial or Short Qualification | Time Trial |
| `/signup time-image` | Whether a screenshot must be attached to each lap time | On |

Turning nationality off removes that question from the wizard entirely. While test mode is on, a separate switch stands in for this one wherever the pictures ask whether you collect nationality, so you can look at both without changing what your real signups ask — see [Testing with test mode](test-mode.md). The time type is only a label — it changes what the questionnaire calls the lap times and nothing else, so pick whichever matches how you actually asked drivers to set them.

```
/signup config view
```

That reads all of it back — channel, both roles, whether the window is open, and all three settings. It is the only signup command that still works while the module is switched off, which makes it a good way to check what you had before you turn it back on.

> **A driver already part-way through keeps the settings they started with.** The wizard takes a copy of your settings the moment a driver presses the button. Change something while people are mid-signup and the change applies to the next driver, not to anyone already going. Nobody gets asked a question that was not there when they began.

---

## Step 4 — Set the availability slots

```
/signup time-slot add day:Tuesday time:20:00
/signup time-slot list
/signup time-slot remove slot_id:2
```

A slot is a day of the week and a time. Add one for each race slot you might use, and drivers will pick the ones they can make. You can have up to 25, and you need at least one — the window will not open without.

Times go in as `20:00` or as `8:00pm`; both work. The list numbers them in day-and-time order, Monday first, and shows them like `#1 — Tuesday 20:00 UTC`.

**Every slot is UTC.** There is no timezone setting anywhere in the module, and the bot writes "UTC" after every time whatever you meant. If your league runs on local time, work out the UTC equivalent before you type it.

Slots are locked while the window is open. Get the list right before you open, because changing it afterwards means closing first.

> **Removing a slot renumbers the ones after it, and old answers do not follow.** The numbers are positions in the list, not permanent labels. Delete `#2` and the old `#3` becomes the new `#2`. Drivers who already told you they were free at `#3` are now recorded as free at a different time, and the export will show the wrong column. If you must edit the list after anyone has signed up, **add** rather than remove, and if you do remove one, check the export against what drivers actually said. See [known issues](../wip-specs/known_issues.md).

---

## Step 5 — Open the window

```
/signup open
/signup open track_ids:1 3 12
/signup open track_ids:1 3 12 close_time:2026-09-01T20:00:00
```

Both parameters are optional.

**`track_ids`** is the list of circuits you want a lap time for. `/track list` shows the IDs. Give three tracks and every driver is asked for three times, one per track, and their total is what seeds them later. Give none and the lap time questions are skipped entirely.

**`close_time`** shuts the window automatically. It is UTC, in the format `2026-09-01T20:00:00`, and it must be in the future.

The bot checks things in order and stops at the first problem: the module must be configured, the window must not already be open, all three of channel and roles must be set, there must be at least one slot, the close time must parse and be in the future, and every track ID must exist.

When it goes through, the bot posts a green **Driver Signups Are Open!** message in your signup channel listing your slots, the tracks, the time type, whether a screenshot is needed, whether nationality is asked, and the auto-close time if you set one. Underneath is the **Sign Up** button, and the message pings your base role.

> **Setting a close time is close to irreversible.** With a timer armed, `/signup close` refuses and tells you to cancel the timer with a command that **does not exist in the bot**. Your only ways out are to let the timer run or to disable the module, which loses your channel and roles. Leave `close_time` off unless you are certain of the date. See [known issues](../wip-specs/known_issues.md).

> **No tracks means no seeding.** Open the window without `track_ids` and nobody submits a lap time, so every approved driver has no total to sort on and the queue falls back to the order you approved people in. That is fine if you never intended to rank by pace — just know that the seed numbers then mean nothing.

Only one window exists per server, and it is not tied to a season or a round. Opening a second one later overwrites the first: there is no signup history, and drivers who signed up before are still signed up.

---

## Step 6 — Approve the drivers who come in

You do not have to do anything to keep the window running. Drivers press the button, the bot makes them a private channel called after their name, and it asks them nine questions in order.

| # | Question | Answered by | Skipped when |
|---|---|---|---|
| 1 | Nationality | Typing | Nationality is switched off |
| 2 | Platform | Buttons — Steam, EA, Xbox, PlayStation | — |
| 3 | Platform ID | Typing | — |
| 4 | Availability | Typing slot numbers, e.g. `1 3 5` | — |
| 5 | Full-Time or Reserve | Buttons | — |
| 6 | Up to three preferred teams, ranked | Buttons, one per team | The driver chose Reserve |
| 7 | Preferred teammate | Typing, or a No Preference button | — |
| 8 | A lap time per track | Typing, plus a screenshot if required | You opened with no tracks |
| 9 | Notes, 50 characters | Typing, or a No Notes button | — |

Question 6 offers your team list, so **add your teams before anyone presses the button** or drivers will be given nothing to choose from. The list is read when a driver starts their signup, not when the window opens, so a team added after `/signup open` still reaches everyone who has not started yet. Teams are core setup — see [Team Commands](../../README.md#team-commands).

When a driver finishes, the bot posts a **Signup Review** panel in their channel summarising every answer, tells them to wait for an admin, and gives you three buttons.

**Approve** grants the complete role, adds up their lap times, and moves them to Unassigned. They are now in the queue for step 8.

**Request Changes** sends them back for one field. You type a reason, then pick which of the nine answers they should redo, and only that question is asked again. When they answer, they come straight back to you.

**Reject** ends it. They are told, with your reason, and are back to Not Signed Up.

Anyone holding the bot role, or Manage Server, can press these.

> **Your next message in that channel becomes the reason.** After pressing Reject or Request Changes, the very next thing you type there is taken as the reason and deleted. Do not press the button and then start chatting to the driver — say your piece first, then press.

> **You have five minutes to choose which field to correct.** Press Request Changes, type the reason, and a row of buttons appears for the nine fields. Leave it too long and the driver goes back to waiting for approval as though nothing happened. Start again.

A driver who goes quiet for 24 hours at any question has their signup cancelled automatically, and their channel is tidied away 24 hours after that. Every finished signup channel goes the same way, whether it ended in approval, rejection or a timeout. A driver who leaves the server has their signup cancelled and their channel deleted at once, and the bot notes it in the log channel.

There are no reminders. The bot never chases a driver who has not signed up, and never re-posts the button.

---

## Step 7 — Close the window

```
/signup close
```

If nobody is mid-signup it closes immediately. Otherwise you get a confirmation listing who is still going, with Confirm and Cancel buttons.

> **One driver is invisible to that check.** Someone you have just sent back with **Request Changes**, before you have picked which field they are to redo, is not counted. If they are the only person in progress, `/signup close` closes on the spot with no confirmation at all.

Closing deletes the Sign Up button, posts a **Signups are now closed** notice in the channel, and tells anyone still filling in the questionnaire that it is over.

> **It drops fewer drivers than it warns you about.** The confirmation says every in-progress driver will be reset, and counts everyone still filling the form in *plus* everyone waiting on you. Only the ones still filling it in are actually dropped. Drivers waiting for your approval, or fixing something you sent back, keep their place — you can still approve them after the window has shut, and you should. See [known issues](../wip-specs/known_issues.md).

> **The confirmation expires after five minutes, silently.** Leave the dialog sitting and the buttons simply stop responding, with nothing to tell you why. Run the command again.

---

## Step 8 — Seed and place your drivers

```
/signup unassigned list
/signup unassigned export
```

`list` shows the queue privately, to you alone: seed number, name, platform, driver type, lap total, preferred teams and teammate, and any notes. `export` sends a CSV you can open in a spreadsheet, also privately — one row per driver, one column per time slot marked `X` where they said they were free, plus their three team preferences and platform details. The CSV is the one to use for anything more than a glance, because availability across a dozen slots is unreadable as text.

> **The CSV is not quite everything `list` shows.** The preferred teammate and the notes are missing from it. If either matters to how you place people, read them off `list` — the export will not carry them.

Seeding adds up the lap times a driver submitted; the lowest total is seed 1. Drivers with no time recorded sort to the bottom, and ties go to whoever **submitted** first — the moment they sent the form in, not the moment you approved it.

> **A driver's total is worked out once, when you approve them, and never again.** There is no way to send an approved driver back for changes, and no command to re-time them. If someone's lap time is wrong, catch it on the review panel with **Request Changes** before you approve.

Then place them:

```
/driver assign user:@Alice division:1 team:Ferrari
/driver unassign user:@Alice division:1
/driver sack user:@Alice
```

`division` takes either the tier number or the division name; `team` must match the team name exactly. **This is the first step that needs a season** — one in setup or already running. Divisions, teams and seasons are core setup; see [Setting up the bot for your league](configuring-the-core-bot.md).

A driver can hold one seat per division, and a team runs out of seats. The Reserve team is the exception: it has room for everyone.

`/driver unassign` takes someone out of a division and returns them to the queue. `/driver sack` removes them from the league altogether, takes back their roles, and returns them to Not Signed Up.

**When roles are granted depends on the season.** This catches people out:

| The season is | Assigning a driver | Unassigning a driver |
|---|---|---|
| In setup | No roles change yet — everything is granted in one go when you approve the season | No roles change, because none were granted |
| Already running | Division and team roles are granted immediately | They are taken back immediately |

The complete role is not part of that. It is granted when you approve the signup, and is not affected by placement either way.

Every assignment and removal deletes the division's lineup message and posts a fresh one, so the channel always holds one current lineup and no history. The lineup channel is set per division with `/division lineup-channel` — see step 6 of the [core guide](configuring-the-core-bot.md). Set it, or the lineup is worked out and posted nowhere.

---

## Step 9 — Try it without real drivers

You will want to see the wizard before your league does. Test mode lets you seat fake drivers and walk the flow without waiting on anybody — see [Test mode](test-mode.md).

The one thing you cannot fake is a second person pressing the button, so it is worth asking one other admin to run a signup through end to end before you announce it.

---

## What your drivers see

**One announcement**, pinging the base role, listing the slots and tracks and what will be asked of them, with a **Sign Up** button underneath.

**A channel of their own** the moment they press it, named after them, visible to them and your admins and nobody else. Nine questions, one at a time, some answered by typing and some by pressing buttons. A red **Cancel Signup** button is on every question, and it is the only way they can back out or start again — there is no edit.

**A summary and a wait.** When they finish, everything they entered is read back to them with a note to wait for an admin to check it.

**One of three endings.** Approved, and they get the complete role and are told so. Sent back for one answer, with your reason. Or rejected, with your reason. In every case the channel disappears a day later.

**A lineup post**, once you have placed them, in whichever channel that division uses.

They never see the queue, their seed, or anyone else's lap times.

---

## What you cannot change

Worth knowing so you do not go looking for the setting.

| What | Why not |
|---|---|
| The nine questions, their wording or their order | Fixed. The only choice you have is whether the nationality and lap time questions appear at all |
| The platform list | Steam, EA, Xbox and PlayStation |
| Three preferred teams | Fixed at three, ranked |
| The 50-character limit on notes | Fixed |
| Slot times being UTC | There is no timezone setting anywhere in the module |
| The 24-hour silence limit, and the 24-hour wait before a channel is deleted | Fixed |
| The five-minute limit on picking a field to correct | Fixed |
| Reminders to drivers who have not signed up | There are none, and no way to re-post the button other than closing and opening again |
| Keeping drivers' lap time screenshots | The bot checks one is attached and never stores it. Save any you want to keep before the channel goes |
| Sending an approved driver back for corrections | There is no route back. Corrections happen before approval or not at all |
| Running two signup windows, or one per round or per season | There is one window per server. Opening a new one writes over the old answers |

---

## Checklist before a season

Worth running through before you open the window.

- [ ] `/module enable signup` has been run
- [ ] Both privileged intents are on, Message Content especially
- [ ] The signup channel is a channel of its own, not one with permissions you care about
- [ ] The base role and the complete role are both set, and are two different roles — one is who may sign up, the other is who got through
- [ ] Your teams are added, so the preferred-team question has something to offer
- [ ] Every slot you might race in is on the list, in UTC, and you are happy with it — because editing it later shifts the numbers
- [ ] You have decided about lap times, and have the track IDs to hand if you want them
- [ ] `/signup config view` shows what you expect
- [ ] You have run one signup end to end yourself
- [ ] `close_time` is left off unless you are sure of the date
- [ ] Each division has a lineup channel, or the lineups go nowhere

---

## If something looks wrong

| What you see | Usually means |
|---|---|
| Every `/signup` command refused | You are outside the interaction channel, or you do not hold the bot role. Administrator does not get you past either |
| `/signup open` refused | Something in the chain is missing — the channel, one of the roles, or any time slot at all. The reply names it |
| A season that will not approve | The signup module is on but missing its channel or a role. The bot names which |
| Drivers press the button and nothing happens after | The Message Content intent is off, so the bot cannot see anything they type |
| The preferred-team question offers nothing but "No Preference" | No teams have been added yet |
| `/signup config channel` errors out | Known: that command is broken. Use `/signup channel` |
| `/signup close` refused, telling you to cancel a timer | Known: you set a `close_time`, and the cancel command does not exist. Wait for the timer, or disable the module and set the channel and roles again |
| Drivers you expected to be dropped by a close are still there | Known: closing only drops drivers still filling the form in. Anyone waiting on you keeps their place — approve them |
| The export shows the wrong availability | Known: a slot was removed, which renumbered the rest. The answers still point at the old numbers |
| Your time slots came back after disabling the module | Known: disabling clears the channel and roles only, whatever the message says |
| A driver stuck waiting after Request Changes | The bot restarted mid-correction, so the five-minute timer is gone and the window will never close on its own. The field buttons still work — pick one and the flow carries on. Failing that, ask them to press **Cancel Signup** and start again |
| Roles not granted after `/driver assign` | The season is still in setup. They are all granted at `/season approve` |
| No lineup posted anywhere | That division has no lineup channel set |
| Seeds that look meaningless | The window was opened with no tracks, so there are no lap times to sort on |
