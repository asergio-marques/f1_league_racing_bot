# Setting up the signup module

Turn the signup module on and your drivers enrol themselves. You post one button; each driver who presses it gets a private channel, works through a short questionnaire, and lands in a queue for you to approve or turn away. What comes out the other end is a list of approved drivers ranked by lap time, ready to be put into teams.

This guide is the **order to do things in**, from switching the module on to a season's placements confirmed and its lineups posted. It sends you to the reference for the fine print:

- **[Signup Module Commands](../../README.md#signup-module-commands)** in the main README — every `/signup` command in full.
- **[Driver Commands](../../README.md#driver-commands)** — the commands that place an approved driver into a team.
- **[Team Commands](../../README.md#team-commands)** — the team list the questionnaire offers.
- **[Module Commands](../../README.md#module-commands)** — turning modules on and off.

You do not need to read those first. Start here.

This guide covers the signup module only. Setting the bot up, creating a season and moving it through its stages, adding divisions, adding rounds and naming a division's lineup channel are a job of their own — follow **[Setting up the bot for your league](configuring-the-core-bot.md)** for those. Where signups depend on one of them, it is named and linked, not explained.

---

## A note on four words

**Wizard** — the private channel a driver gets when they press the button, and the run of questions inside it. One per driver, named after them, deleted 24 hours after their signup ends however it ends.

**Slot** — a day and time you offer as a race slot, so drivers can say which ones they can make. You set the list; they tick the ones that suit. Nothing else in the bot uses them — they exist so you can work out who can race when.

**Seed** — a driver's rank in the approved queue, worked out by adding up the lap times they submitted. Fastest total is seed 1. It is a sorting aid for you, not something the bot acts on.

**Unassigned** — an approved driver who is in no team yet. This is a state on the driver, not on their signup, and it is the whole point of the exercise: signups turn strangers into Unassigned drivers, and placement turns Unassigned drivers into a lineup.

A driver's status lives on the driver, not on their signup form. "Reserve" is an answer they gave to a question, not a status. "Withdrawn", "rejected" and "timed out" all land in the same place — Not Signed Up, as though they had never started.

---

## Before you start

**The bot must already be set up.** You need the interaction channel, the interaction role and the log channel in place, and your league's **base role** and **driver role** set with `/bot base-role` and `/bot driver-role`. If you have not got that far, follow [Setting up the bot for your league](configuring-the-core-bot.md#step-1--tell-the-bot-who-is-in-charge) first; this guide picks up from there.

**Both privileged intents must be on.** The signup module is the one part of the bot that needs both. Without **Server Members** it cannot grant or revoke a single role; without **Message Content** the wizard never sees a word your drivers type, and every signup stalls on question one. See [Privileged Gateway Intents](../../README.md#privileged-gateway-intents).

**Signups belong to a season.** Drivers sign up for each season, not once for the league: when a season ends, every driver returns to Not Signed Up and signs up again for the next. You can switch the module on and configure it with no season at all, but a window opens only for a season whose configuration is confirmed — see steps 5 to 7 of [the core guide](configuring-the-core-bot.md#step-5--start-the-season) for the stages a season passes through.

**Who is allowed to run what.** Every command below also has to be run in the bot's usual command channel by someone with the usual bot role.

| Commands | You need |
|---|---|
| `/module enable signup` and `/module disable signup` | The **league admin role** |
| `/driver sack` | The **league admin role** |
| Everything under `/signup`, including `/signup channel` | The **interaction role** |
| `/driver assign`, `/driver unassign`, `/driver reject`, `/driver move`, `/driver release`, `/driver reassign` | The **interaction role** |

> **Both tiers are roles, and Discord's permissions are not one of them.** Someone holding Administrator but neither role is refused every command here, and so is anyone running one outside the interaction channel. Being the server owner does not get you past it — give yourself one of the two roles instead.
>
> The league admin role carries the interaction role's tier within it, so a league admin needs only the one role. Note the asymmetry in the table above: unassigning a driver is a league manager's, because assigning them again puts it back; sacking one is a league admin's, because nothing does. Releasing a driver from one division is the deliberate exception — it is a league manager's, because it is part of running a lineup from week to week.

---

## Step 1 — Switch it on

```
/module enable signup
```

The signup module can be switched on or off only while no season is live, or while the season is in configuration. Confirming the season's configuration fixes it, on or off, until that season ends — so decide before then.

Nothing is configured yet. The bot creates an empty configuration record and tells you what to run next: `/signup channel`, which is step 2, and `/bot base-role` or `/bot driver-role` if either role is not set yet. The question settings are not written at this point at all — until you change one, the bot simply falls back to its defaults.

Turning the module off force-closes an open window, hands back the permissions it applied to your signup channel, and forgets your channel.

> **Turning it off keeps more than you might expect.** Your time slots, your three question settings, and every signup drivers have already submitted all survive, and so do the league's two roles, which were never the module's. Only the channel is forgotten, and the bot's reply says so. Switch the module back on and you will find your old slots waiting.

---

## Step 2 — Give it a channel

```
/signup channel channel:#signups
```

The module does nothing at all until it has a channel, and the league's two roles beside it. The command asks for the interaction role like the rest of `/signup` — naming a channel is configuring a module, not governing the bot.

**The channel** is where the Sign Up button gets posted. Setting it rewrites the channel's permissions: everyone loses sight of it, the base role can see it but cannot type in it, and holders of either league role can see and type. That is deliberate — the only thing a driver should be doing in there is pressing the button.

**The two roles are your league's, not this module's**, and are set in the [core guide](configuring-the-core-bot.md#step-1--tell-the-bot-who-is-in-charge). Here is what signups do with them. **The base role** is who the signups are for: it decides who can see the channel, and it is the role that gets pinged when you open the window. Change it and the bot moves the channel's permissions to the new role. **The driver role** is the reward: the bot grants it the moment you approve a driver, so it is the badge that says someone is through the door. That means the bot has to be able to grant it — its own role must sit above the driver role in your server's role list, and it needs **Manage Roles** — and `/bot driver-role` refuses one it cannot. If either role is later deleted from the server, you can replace it even mid-season; the core guide says how.

> **Use a channel of its own, and nothing else.** Setting the signup channel replaces every permission override on it, and moving the signup channel elsewhere strips the old one bare on the way out. Any permissions you had set up by hand go with them.

> **The signup channel cannot be your bot command channel.** The bot refuses outright. They are doing opposite jobs — one is for drivers who may not type, the other is for admins who must.

> **Do not use `/signup config channel`.** The old command is broken and fails with an error whatever you pass it. `/signup channel` is the one that works.

**These three block a season.** While the signup module is on, `/season config-review` withholds its button until the channel and both roles are set, and names the ones that are missing. If you are not going to use signups this season, turn the module off rather than leaving it half-configured.

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

> **Every driver of a season is asked the same questions.** These settings, like everything in steps 2 to 4, are fixed from the moment the season's configuration is confirmed until it ends, and the bot keeps a copy with the season — so a signup read back a season later still shows what it was asked under.

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

**The numbers move, and that is fine.** They are positions in the list, not permanent labels. Add a Monday morning slot and everything after it counts up by one; delete `#2` and the old `#3` becomes the new `#2`. What your drivers told you does not move with them — their availability is recorded against the slot itself, so editing the list never changes anyone's answer and the export always marks the times they actually chose. Remove a slot and put it back at the same day and time, and the drivers who chose it are on it again.

**The list is fixed once the season's configuration is confirmed.** So is every other setting in this guide — the channel, both roles, nationality, time type and time image, and turning the module on or off. They work while no season is active, or while the season is in configuration, and are refused from confirmation until that season ends: the season's signups are made under them. Get the list right before you run `/season config-review`.

---

## Step 5 — Confirm the season's configuration

```
/season setup game_edition:25
/season config-review
```

The window opens for a season, so a season has to be started and its configuration confirmed first. That is core setup, covered in [the core guide](configuring-the-core-bot.md#step-6--confirm-the-configuration); what matters here is that confirming it fixes every setting in steps 1 to 4, and that the season then **waits for its signup window**.

---

## Step 6 — Open the window

```
/signup open
/signup open track_ids:1 3 12
/signup open track_ids:1 3 12 close_time:2026-09-01T20:00:00
```

Both parameters are optional.

**`track_ids`** is the list of circuits you want a lap time for. `/track list` shows the IDs. Give three tracks and every driver is asked for three times, one per track, and their total is what seeds them later. Give none and the lap time questions are skipped entirely.

**`close_time`** shuts the window automatically. It is UTC, in the format `2026-09-01T20:00:00`, and it must be in the future.

**The window belongs to a season.** You can open it once the season's configuration is confirmed and it is waiting for its signup window, or mid-season while it is ongoing with no placements left to confirm. Opening it moves the season on — to signups, or mid-season to ongoing, signups.

The bot checks things in order and stops at the first problem: test mode must be off, the module must be configured, the window must not already be open, the season must be waiting or ongoing, all three of channel and roles must be set — with both roles still on the server and the driver role one the bot can grant, every fault among these named together — there must be at least one slot, the close time must parse and be in the future, and every track ID must exist.

> **A test-mode season never opens a window.** No real driver may sign up while the server is in test mode — the Sign Up button refuses them too — and a season confirmed in test mode goes straight to placements. Test mode is set in configuration and switched off when the season ends; see Step 10.

When it goes through, the bot posts a green **Driver Signups Are Open!** message in your signup channel listing your slots, the tracks, the time type, whether a screenshot is needed, whether nationality is asked, and the auto-close time if you set one. Underneath is the **Sign Up** button, and the message pings your base role.

> **A close time is not a commitment.** Set one here if you like, or leave `close_time` off and add it later with `/signup close-time add`. Either way you can move it with `/signup close-time modify` or clear it outright with `/signup close-time cancel` — Step 8 covers both. Mistyping the day or the year costs you one command, not your configuration.

> **No tracks means no seeding.** Open the window without `track_ids` and nobody submits a lap time, so every approved driver has no total to sort on and the queue falls back to the order they sent their signups in. That is fine if you never intended to rank by pace — just know that the seed numbers then mean nothing.

**Every signup is kept.** Each belongs to the season and the window it came through, with the tracks that window asked for and its close time, and nothing overwrites it — not a second window, and not the next season. A season deleted with `/season abort` takes its signups with it; a completed or cancelled season keeps them.

---

## Step 7 — Approve the drivers who come in

You do not have to do anything to keep the window running. Drivers press the button, the bot makes them a private channel called after their name, and it asks them nine questions in order.

| # | Question | Answered by | Skipped when |
|---|---|---|---|
| 1 | Nationality | Typing | Nationality is switched off |
| 2 | Platform | Buttons — Steam, EA, Xbox, PlayStation | — |
| 3 | Platform ID | Typing | — |
| 4 | Availability | Typing slot numbers, e.g. `1 3 5` | — |
| 5 | Full-Time or Reserve | Buttons | — |
| 6 | Up to three preferred teams, ranked | Buttons, one per team, each showing the team's full name | The driver chose Reserve |
| 7 | Preferred teammate | Typing, or a No Preference button | — |
| 8 | A lap time per track | Typing, plus a screenshot if required | You opened with no tracks |
| 9 | Notes, 50 characters | Typing, or a No Notes button | — |

Question 6 offers your team list, so **add your teams before you confirm the season's configuration** — the list is fixed from then until the season ends, and drivers would otherwise be given nothing to choose from. Teams are core setup — see [Team Commands](../../README.md#team-commands).

**Lap times are written exactly.** A driver types each one as `1:23.456` — a dot and exactly three digits after it. `1:23:456` or `1:23.4` is refused and the time asked for again, so a seed is never built on a guess. It is the form your results pastes use too.

**Typed answers cannot ping anybody.** A platform ID, preferred teammate or note holding a role mention, `@everyone` or `@here` is refused and asked again, since the review panel would otherwise notify everyone who can see the channel. Emoji and formatting in those answers are kept.

When a driver finishes, the bot posts a **Signup Review** panel in their channel summarising every answer, tells them to wait for an admin, and gives you three buttons.

**Approve** grants the driver role, adds up their lap times, and moves them to Unassigned. They are now in the queue for step 9.

**Request Changes** sends them back for one field. You type a reason, then pick which of the nine answers they should redo, and only that question is asked again. When they answer, they come straight back to you.

**Reject** ends it. They are told, with your reason, and are back to Not Signed Up.

Anyone holding the interaction role or the league admin role can press these. The driver whose signup it is cannot — they can read the channel, so the buttons check who is pressing.

> **Your next message in that channel becomes the reason.** After pressing Reject or Request Changes, the very next thing you type there is taken as the reason and deleted. Do not press the button and then start chatting to the driver — say your piece first, then press.

> **You have five minutes to choose which field to correct.** Press Request Changes, type the reason, and a row of buttons appears for the nine fields. Leave it too long and the driver goes back to waiting for approval as though nothing happened. Start again.
>
> **You get pinged when that happens, in the driver's channel.** The bot names the driver, says the five minutes lapsed, and mentions you — so a window you walked away from comes back to you rather than going quiet. The driver reads that channel and will see the review has returned to you.
>
> **A restart sends them back too, whatever time was left.** If the bot restarts while the field buttons are up, the driver returns to waiting for approval immediately and you are pinged the same way, with the message saying the bot restarted. The five minutes cannot run while the bot is down, and you are no longer sat there choosing — so the window is closed rather than left open for a press that would never come. Press Request Changes again when you are ready.

A driver who goes quiet for 24 hours at any question has their signup cancelled automatically, and their channel is tidied away 24 hours after that. Every finished signup channel goes the same way, whether it ended in approval, rejection or a timeout. A driver who leaves the server has their signup cancelled and their channel deleted at once, and the bot notes it in the log channel.

There are no reminders. The bot never chases a driver who has not signed up, and never re-posts the button.

---

## Step 8 — Close the window

```
/signup close
```

If nobody is mid-signup it closes immediately. Otherwise you get a confirmation listing who is still going, with Confirm and Cancel buttons.

**If you set a close time, clear it first.** `/signup close` refuses while one is armed, names the time it is waiting for, and sends you here:

```
/signup close-time cancel
/signup close
```

Closing ahead of the time you set is two steps on purpose — the first is hard to do by accident. If you only want to move the deadline rather than close now, use `/signup close-time modify` with the new time and leave the window running.

Closing deletes the Sign Up button, posts a **Signups are now closed** notice in the channel, and tells anyone still filling in the questionnaire that it is over.

**Closing moves the season on**, whether you close it or its close time does. Before the season has started, it moves to placements, where you build divisions and place drivers. Mid-season, it moves to placements for the new drivers where anyone is still Unassigned, awaiting approval or mid-correction, and straight back to ongoing where nobody is.

> **A mid-season window ends with the season's last division.** Once every division is finished or cancelled there is no round left to place anyone into: the window is closed for you, and every driver still waiting — unplaced, unconfirmed, awaiting approval or mid-correction — is turned down and returns to Not Signed Up.

> **It drops fewer drivers than it warns you about.** The confirmation says every in-progress driver will be reset, and counts everyone still filling the form in *plus* everyone waiting on you. Only the ones still filling it in are actually dropped. Drivers waiting for your approval, fixing something you sent back, or sat with the field buttons up keep their place — you can still approve them after the window has shut, and you should. See [#128](https://github.com/asergio-marques/f1_league_racing_bot/issues/128).
>
> **Closing is not a deadline for your own review.** Request Changes still works after the window has shut, so a driver you send back then behaves exactly as they would have before it — including the five-minute field window and the ping if you leave it.

> **The confirmation expires after five minutes, silently.** Leave the dialog sitting and the buttons simply stop responding, with nothing to tell you why. Run the command again.

---

## Step 9 — Seed and place your drivers

```
/signup unassigned list
/signup unassigned export
```

Both read the season's signups: every driver still to be settled — Unassigned, awaiting approval, or correcting their signup — with what they entered this season. `list` shows the queue privately, to you alone: seed number, name, platform, driver type, lap total, availability, preferred teams and teammate, and any notes. `export` sends a CSV you can open in a spreadsheet, also privately — one row per driver, one column per time slot marked `X` where they said they were free, plus their three team preferences and platform details.

Use `list` for a glance — checking one driver, or sanity-checking a division you have just filled — and the CSV for a full pass over the field. `list` names every slot a driver picked, so it answers "can this driver make Tuesday?" on its own; the CSV lays the whole field out as a grid, which is what you want when you are building divisions around the times rather than asking after one person.

> **The CSV is not quite everything `list` shows.** The preferred teammate and the notes are missing from it. If either matters to how you place people, read them off `list` — the export will not carry them.

Seeding adds up the lap times a driver submitted; the lowest total is seed 1. Drivers with no time recorded sort to the bottom, and ties go to whoever **submitted** first — the moment they sent the form in, not the moment you approved it.

> **A driver's total is worked out once, when you approve them, and never again.** There is no way to send an approved driver back for changes, and no command to re-time them. If someone's lap time is wrong, catch it on the review panel with **Request Changes** before you approve.

Then settle every one of them:

```
/driver assign user:@Alice division:1 team:Ferrari
/driver unassign user:@Alice division:1
/driver reject user:@Bob
```

`division` takes either the tier number or the division name; `team` takes the team's **shorthand**, which the command suggests as you type. These work only while the season is **in placements**, or mid-season while the drivers of a closed window are placed — the divisions have to exist first, and building them is core setup; see [Setting up the bot for your league](configuring-the-core-bot.md#step-8--build-the-season).

**Every signup must be settled before placements are confirmed.** Place each Unassigned driver with `/driver assign`, or turn them down with `/driver reject`, which returns them to Not Signed Up and takes the driver role back; their signup stays with the season. A signup still in review is settled from its review panel. `/season placements-review` names anyone left, and withholds its button until nobody is.

A driver can hold one seat per division, and a team runs out of seats. The Reserve team is the exception: it has room for everyone.

`/driver assign` is refused while test mode is active — a real driver is never seated under test. Fake drivers are placed by `/test-mode roster add` and are unaffected.

**Nothing is granted or posted when you assign.** A placement stands outside the championship until placements are confirmed from `/season placements-review`: that is when every placed driver is granted their division and team roles, and every lineup is posted. Until then `/driver unassign` takes a placement back without trace, returning the driver to the queue. The driver role is not part of this — it is granted when you approve the signup.

**Once placements are confirmed, the lineup changes differently.** `/driver move` moves a driver to another team or division, `/driver release` takes them out of one division while they keep their others, and `/driver sack` removes them from the season. Each takes effect at once, roles and lineup included. Sacking deletes nobody: a sacked driver may sign up again in a later window.

The lineup channel is set per division with `/division lineup-channel` — see step 9 of the [core guide](configuring-the-core-bot.md). Confirming placements refuses a division without one, or whose lineup channel has since been deleted from the server — mid-season too, since the confirmation posts the lineup of every division a new driver joins.

---

## Step 10 — Try it without real drivers

You will want to see the wizard before your league does. Test mode lets you seat fake drivers and walk the flow without waiting on anybody — see [Test mode](test-mode.md).

**A test belongs to a season of its own.** Test mode can be switched on only while a season is in configuration, and a season confirmed with it on never opens a signup window — it goes straight to placements, where fake drivers are seated. While it is on, nobody real can sign up or be placed. It switches itself off when that season is completed, cancelled or aborted, and every fake driver is deleted with it; abort the test season with `/season abort` and start the real one.

The one thing you cannot fake is a second person pressing the button, so it is worth asking one other admin to run a signup through end to end before you announce it.

---

## What your drivers see

**One announcement**, pinging the base role, listing the slots and tracks and what will be asked of them, with a **Sign Up** button underneath.

**A channel of their own** the moment they press it, named after them, visible to them and your admins and nobody else. Nine questions, one at a time, some answered by typing and some by pressing buttons. A red **Cancel Signup** button is on every question, and it is the only way they can back out or start again — there is no edit.

**A summary and a wait.** When they finish, everything they entered is read back to them with a note to wait for an admin to check it.

**One of three endings.** Approved, and they get the driver role and are told so. Sent back for one answer, with your reason. Or rejected, with your reason. In every case the channel disappears a day later.

**A lineup post**, once placements are confirmed, in whichever channel that division uses.

**The same button next season.** When a season ends they return to Not Signed Up, lose the driver role, and sign up again for the next one.

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
| Running two signup windows at once | One window is open at a time. A season may run several in turn — one before it starts and any number mid-season — and every signup from each is kept |
| Signing up once for every season | Drivers sign up for each season. When one ends, every driver returns to Not Signed Up |

---

## Checklist before a season

Worth running through before you confirm the season's configuration, which fixes everything on it.

- [ ] `/module enable signup` has been run
- [ ] Both privileged intents are on, Message Content especially
- [ ] The signup channel is a channel of its own, not one with permissions you care about
- [ ] The base role and the driver role are both set, and are two different roles — one is who may sign up, the other is who got through
- [ ] The bot's own role sits above the driver role in your server's role list, so it can grant it
- [ ] Your teams are added, so the preferred-team question has something to offer
- [ ] Every slot you might race in is on the list, in UTC, and you are happy with it — because the list is fixed once the season's configuration is confirmed
- [ ] You have decided about lap times, and have the track IDs to hand if you want them
- [ ] `/signup config view` shows what you expect
- [ ] `close_time` is the date you meant, if you are setting one — `/signup close-time modify` moves it later if not
- [ ] Once the window closes: each division has every channel it posts to, and every signup is settled before you confirm placements

---

## If something looks wrong

| What you see | Usually means |
|---|---|
| Every `/signup` command refused | You are outside the interaction channel, or you hold neither the interaction role nor the league admin role. Discord's Administrator permission does not get you past either |
| `/signup open` refused | Something in the chain is missing — the channel, one of the roles, or any time slot at all — or the season is not waiting for a window: its configuration is unconfirmed, or it is mid-way through placements. The reply names it |
| `/signup open` refused, naming a role no longer on the server | That role was deleted. Replace it with `/bot base-role` or `/bot driver-role` — allowed mid-season for a deleted role — and the bot gives the new one to every driver |
| `/signup open` refused, saying the driver role sits above the bot's | Move the bot's own role above the driver role in your server's role list, then open again |
| `/season config-review` offers no button | The signup module is on but missing its channel or a role, a role has been deleted, or the bot cannot grant the driver role. The review names which |
| `/season placements-review` offers no button, naming drivers | Those signups are unsettled. Place or reject each driver, or finish reviewing their signup |
| Drivers press the button and nothing happens after | The Message Content intent is off, so the bot cannot see anything they type |
| The preferred-team question offers nothing but "No Preference" | No teams have been added yet |
| `/signup config channel` errors out | Known: that command is broken. Use `/signup channel` |
| `/signup close` refused, naming an auto-close time | You set a `close_time`. Run `/signup close-time cancel`, then `/signup close` again — or `/signup close-time modify` if you only want to move the deadline |
| Drivers you expected to be dropped by a close are still there | Known: closing only drops drivers still filling the form in. Anyone waiting on you keeps their place — approve them |
| `/signup time-slot add`, `remove` or another signup setting refused, naming a season | That season's configuration is confirmed, so its signup settings are fixed until it ends |
| Your time slots came back after disabling the module | Known: disabling clears the channel and roles only, whatever the message says |
| A driver went back to waiting for approval on their own | The five-minute field window lapsed, or the bot restarted while it was open. The ping in their channel says which. Press **Request Changes** again |
| Roles not granted after `/driver assign` | Placements are not confirmed yet. They are all granted when you confirm them from `/season placements-review` |
| A driver cannot sign up, saying they are already approved | They are still Unassigned or placed this season. They sign up again once the season ends |
| No lineup posted after an assignment | Lineups are posted when placements are confirmed, not when a driver is assigned |
| Seeds that look meaningless | The window was opened with no tracks, so there are no lap times to sort on |
