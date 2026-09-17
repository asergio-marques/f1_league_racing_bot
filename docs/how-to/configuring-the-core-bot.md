# Setting up the bot for your league

Everything the bot does sits on top of one thing: a **season**, divided into **divisions**, each running a list of **rounds**. This guide is the **order to do things in**, from an invited bot to a confirmed season racing on Sunday, and on to its end.

It covers only the parts every league needs, whichever features you use. The five optional modules — signup, results & standings, attendance, weather and images — each have their own settings, and this guide tells you when to turn one on, not how to configure it.

It sends you to the reference for the fine print:

- **[The main README](../../README.md)** — every command in full, with its parameters and who may run it.
- **[Configuring the signup module](configuring-the-signup-module.md)** — if you want your drivers enrolling themselves, and help putting the ones who do into teams.
- **[Configuring the results & standings module](configuring-the-results-module.md)** — if you want results submitted, points scored and championship tables kept.
- **[Configuring the attendance module](configuring-the-attendance-module.md)** — if you want the bot asking your drivers whether they are racing, and keeping score of who does not.
- **[Configuring the weather module](configuring-the-weather-module.md)** — if you want generated forecasts before each round.
- **[Configuring the image module](configuring-the-image-module.md)** — if you want the bot posting pictures rather than text.

You do not need to read those first. Start here.

---

## A note on some words

**Division** — one championship within your league, with its own drivers, its own calendar and its own Discord role. A one-division league is perfectly normal; the bot simply has one of them.

**Tier** — where a division sits in the pecking order. Tier 1 is your top division. Every division needs one, they must all be different, and across the season they must run **1, 2, 3…** with no gaps. Tier is not the same as the division's name: you might call tier 1 "Pro" and tier 2 "Academy".

**Round** — one race weekend in a division's calendar. You never type a round number; the bot works them out by sorting your rounds by date.

**Stage** — where a season has got to. This matters more than anything else in this guide, because it decides which commands will even run. A season moves through them in one direction:

| Stage | What happens in it | How it ends |
|---|---|---|
| **Configuration** | You settle the team list, the modules and their settings, and test mode | `/season config-review`, confirmed |
| **Waiting** | Nothing yet — the season waits for its signup window. Skipped where the signup module is off or test mode is on | `/signup open` |
| **Signups** | Drivers sign up | The window closes |
| **Placements** | You build the divisions and calendar, and place the drivers | `/season placements-review`, confirmed |
| **Ongoing** | The season races. A new signup window may open mid-season, and its drivers are placed and confirmed the same way | Every division finishes |
| **Pending completion** | Every division is finished or cancelled | `/season complete` |

A season is **live** from `/season setup` until it is completed, cancelled or aborted, and a server holds one live season at a time. Divisions and rounds are created and deleted only in Placements; once a season is ongoing you can only amend and cancel.

---

## Before you start

**Somebody has to run the bot on a computer.** It is not a service you invite and forget — it is a program that must stay running for forecasts to post and results to be collected. If it is switched off, nothing happens while it is down. Some of it is picked up when it starts again — missed weather phases, missed check-in deadlines and a signup window's auto-close timer are all recovered — but anything else that came due is simply missed.

Three things have to be done on that computer, by hand, before any command in this guide works. They are covered in [Setup](../../README.md#setup) in the main README:

1. **Install it and give it your bot token** — the token comes from Discord's developer portal and goes in a file called `.env`.
2. **Turn on two switches in that same portal** — the *Server Members* and *Message Content* intents. Without the first the bot cannot hand out roles at all. See [Privileged Gateway Intents](../../README.md#privileged-gateway-intents).
3. **Start it once.** It builds its own databases on first run — two of them, `bot.db` for your league's records and `scheduler.db` for work it has scheduled ahead. There is nothing to create.

> **If you keep backups, keep both.** `bot.db` on its own is not a complete backup: without `scheduler.db` a restored season still knows its rounds, but every weather phase, RSVP notice and result submission it was waiting to send has gone, and only reviewing and approving the season again brings them back. Copying `bot.db` while the bot is running can also miss the most recent changes, because they may still be sitting in a `bot.db-wal` file next to it. The safe ways are in [Backing up](../../README.md#backing-up). Backups are optional and entirely your choice — but a half-backup is worse than none, because it looks complete.

**Invite it with the right permissions.** All of them listed under [Required Permissions](../../README.md#required-permissions) are genuinely used, and the two worth checking twice are **Manage Roles** — the bot cannot place a driver without it — and **Mention @everyone, @here, and All Roles**, which it needs to ping a division role even though it never pings everyone.

> **The bot's own role must sit above the roles it hands out.** Discord will not let it grant a role positioned above its own, whatever permissions it has. Drag the bot's role up your server's role list before you start assigning drivers.

If somebody else hosts the bot for you, you need their help for those three steps and nothing else. Everything below you do yourself, from Discord.

---

## Step 1 — Tell the bot who is in charge

```
/bot-init interaction_role:@Stewards league_admin_role:@Owners interaction_channel:#bot-commands log_channel:#bot-logs
```

Four decisions, and they shape everything after:

| What you set | What it means |
|---|---|
| **Interaction role** | The role a person must hold to command the bot at all |
| **League admin role** | The role a person must hold to govern the bot, and to do anything that cannot be undone |
| **Interaction channel** | The **only** channel the bot accepts commands in |
| **Log channel** | Where the bot explains itself — what it worked out, what it could not find, why something fell back |

Run it anywhere, and run it as a server administrator: until it has run there is no command channel to run it in and no league admin role to hold. It is one of five commands on that footing, and the other four are below.

**Give the two roles to different people, or the same people — that is your call.** The league admin role is the smaller circle: it is who may cancel a season, sack a driver or wipe the bot. The interaction role is everyone who runs race weekends. Anyone holding the admin role can do everything the interaction role can, so there is no need to give somebody both.

**Pick a private channel for logs.** The bot writes a great deal there — every command that succeeded, every picture it could not draw, every driver it could not place. It is written for you, not for your drivers.

**Two levels of permission, and both are roles you pick.**

| To run | You need |
|---|---|
| Most commands in this guide | The **interaction role** |
| Anything that cannot be undone: `/bot-reset`, `/clean-bot`, `/module enable` and `/module disable`, cancelling or deleting a season, division or round, aborting or completing a season, `/team remove`, `/driver sack`, and every `/test-mode` command | The **league admin role** |
| Confirming a season's configuration or its placements, on the button `/season config-review` or `/season placements-review` posts | Whoever ran that review, or the league admin role |
| `/bot-init` and the four commands that change one setting | The league admin role **or** Discord's **Administrator** permission, from any channel |

The league admin role covers everything the interaction role does, so whoever holds it can
run everything in this guide without also being given the interaction role.

**Discord's own permissions do not come into it.** Administrator and Manage Server govern
your *server*; these two roles govern your *league*, and the bot reads only the roles. The
one exception is the five setup commands in the row above — they take Administrator too,
because they are what repairs the settings everything else depends on.

So the interaction role is not a licence to reconfigure the league — it is the gate
everything else sits behind. Drivers do not need it.

**`/bot-init` runs once.** Run it a second time and it politely refuses — it will not overwrite what is already there. To change one of the four settings afterwards, use the command for that setting:

```
/bot-log-channel channel:#new-bot-logs
/bot-interaction-channel channel:#new-bot-commands
/bot-interaction-role role:@NewStewards
/bot-admin-role role:@NewOwners
```

Each changes that one setting and touches nothing else.

> **These work from any channel, and a server administrator can run them.** That is on purpose. They exist for the day something goes wrong with the four settings themselves — somebody deletes the log channel, archives the command channel, or removes one of the two roles. If they needed the command channel or a league role to run, the one thing you could not repair would be the thing that had broken; and if they needed the league admin role, a server that had lost that role could never get it back. So these five, and only these five, also accept Discord's **Administrator** permission.
>
> `/bot-reset` is **not** one of them. It destroys the settings rather than repairing them, so it asks for the league admin role and is given in the command channel like everything else.

If you would rather start over from nothing, `/bot-reset full:True` clears the configuration and `/bot-init` becomes available again.

> **A command run in the wrong channel is refused, not ignored.** You get a short message only you can see. If a command seems to do nothing, check which channel you are in first.

**One team already exists.** `/bot-init` creates the **Reserve** team, which has unlimited seats and belongs to every division. You cannot remove or rename it. Nothing else is created — your team list starts empty apart from it.

---

## Step 2 — Turn on the modules you want

```
/module enable results
```

Five modules, **all off to begin with**. The bot works without any of them, but a bare bot only holds a calendar.

| Module | What it adds |
|---|---|
| `signup` | A sign-up process drivers work through themselves, in their own private channel — see [its own guide](configuring-the-signup-module.md) |
| `results` | Result submission, points, standings, penalties and appeals — see [its own guide](configuring-the-results-module.md) |
| `attendance` | Check-in calls before a race, attendance records, and penalties for missing one — see [its own guide](configuring-the-attendance-module.md) |
| `weather` | Generated forecasts published in three phases before each round — see [its own guide](configuring-the-weather-module.md) |
| `images` | Posts pictures instead of text — see [its own guide](configuring-the-image-module.md) |

**Order matters, and so does timing:**

| Module | The rule |
|---|---|
| `attendance` | Turn `results` on first. Attendance is refused without it |
| `signup` | Switched on or off only with no season, or while the season is in configuration. Confirming the configuration fixes it |
| `weather`, `results`, `attendance`, `images` | Cannot be turned **on** once a season's placements are confirmed. Decide before then |
| Any module | Cannot be turned **off** while a season is pending completion |

> **Turning one off is not guarded the way turning it on is.** No module but signup can be *enabled* mid-season, but `results` and `attendance` can still be *disabled* mid-season. You are no longer allowed to do it unawares: each asks you to confirm and tells you what it costs. Turning `results` off mid-season is the expensive one — it deletes the season's results entire.

> **Turning `results` off takes `attendance` with it.** If attendance is on, the bot warns you what the cascade will take and disables nothing until you confirm; the reply that follows names both modules, and the log channel records it as well.

> **And if a season is running, turning `results` off destroys that season's championship.** Every classification recorded, every standing computed from them, and every results and standings message already posted are deleted; every round still waiting on results, report verdicts or appeal verdicts is closed as having run without results, which is what lets you complete the season afterwards. Verdicts already announced stay in the verdicts channel — the bot cannot take those back. Your points configurations and your division channels are kept. You are warned and must confirm, and none of it can be undone. See [Configuring the results module](configuring-the-results-module.md).

**Turning a module off clears less than you would expect.** Only the signup module behaves the way the phrase suggests: it forgets its channel and its two roles, though it keeps its time slots and its question settings whatever the message says. Of the rest, `attendance` forgets only which channels each division posts to, and `weather`, `results` and `images` forget no *configuration* at all — every channel, deadline, penalty value and points configuration survives, and the module comes back as it was. What `results` does destroy, where a season is running, is that season's results themselves, which are not configuration; see the call-out above. What weather does do is cancel its own scheduled jobs — the forecasts for every remaining round. Turning a module off stops that module's work and nobody else's: the jobs belonging to the modules you left on go on running.

Each module then has its own configuration, which is not covered here. Start from [Slash Commands](../../README.md#slash-commands) in the README and find that module's section.

> **Decide now, not later.** Signup is fixed the moment a season's configuration is confirmed, and none of the other four can be turned *on* once its placements are. Turning `results` or `attendance` off mid-season does real and irreversible damage — `results` deletes the season's championship outright — and each module you turn on adds to what confirming a season demands of you. This step being early is not an accident.

---

## Step 3 — Build your team list

```
/team add name:Red Bull role:@Red Bull
/team list
```

A team belongs to the **server**, not to a season, so you do this once and it carries forward. Every team needs a Discord role — that role is granted to a driver when you seat them and taken away when you do not.

The rules for a team's **name** are checked the moment you set it, and are listed in full under [`/team add`](../../README.md#team-commands). The short version: a name has to stay distinct from every other team once punctuation and accents are stripped, so `Red Bull` and `Red  Bull!` cannot both exist — they would draw the same badge file. A name may start with a digit; `2 Fast` is fine.

> **These rules apply whether or not you ever use pictures.** A name is only cheap to fix at the moment you set it, so the bot constrains it then, rather than leaving you stuck with a name you cannot correct without losing that team's history.

**Set the Reserve team's role too:**

```
/team reserve-role role:@Reserve
```

This is easy to forget and the bot warns you about it at every season review, because a driver sitting in Reserve without it will be rejected when results are submitted.

Every division you create later is built from this list, each team with **two seats**. Build the list before you confirm a season's configuration: from that moment until the season ends, `/team add`, `/team remove` and `/team rename` are refused. A team's role is the exception — if a role is deleted from the server, point the team at its replacement with `/team role`, in any state. Use `/team list` to see the whole list with its roles, and `/team lineup` once drivers are placed.

---

## Step 4 — Look at the circuit list

```
/track list
```

The bot carries **28 circuits**, each with an ID and a full name — `12` is Silverstone Circuit, `22` is Autódromo José Carlos Pace. You use either when adding a round, and autocomplete offers them as you type. Picking a suggestion is the easiest route, but you are not tied to it: the ID on its own, the name in any capitalisation, and the whole line as the dropdown shows it (`12 – Silverstone Circuit`) all work, so retyping or pasting an entry is safe. Where an ID and a name appear together, the ID decides.

**The list is fixed.** You cannot add a circuit of your own, rename one, or change how wet the bot thinks it is. Each circuit's rainfall behaviour ships with the bot and is the same on every server.

Worth a look now rather than mid-way through building a calendar, because the names are the official circuit names rather than country names, and they are what a round is stored under. The full list is also in the README under [Track ID Reference](../../README.md#track-id-reference).

---

## Step 5 — Start the season

```
/season setup game_edition:25
```

The game edition is the year of the F1 game you are racing on — `25` for F1 25. The bot numbers the season for you.

The season is now **in configuration**. Nothing you do from here is live until its placements are confirmed.

**This is the last moment steps 2 and 3 are open.** Configuration is where the season's settings are decided, and confirming it fixes them until the season ends:

- the **team list** — `/team add`, `/team remove` and `/team rename`;
- the **signup module** — turning it on or off, and every setting in [its guide](configuring-the-signup-module.md);
- **test mode**, which can be switched on or off only now — see [Testing with test mode](test-mode.md);
- and the game edition you just gave.

The other modules' settings stay open after confirmation, but none of them can be turned **on** once placements are confirmed. If your league attaches points configurations to its seasons, attach them now — see [Configuring the results module](configuring-the-results-module.md).

---

## Step 6 — Confirm the configuration

```
/season config-review
```

The bot posts the configuration to the channel: test mode, which modules are on, the team list with its roles, and — with the image module on — the image outputs. Then it checks everything that can be checked before the season has any divisions:

| The check | Only with |
|---|---|
| The signup channel, base role and complete role are set | `signup` |
| Every team name can be used | — |
| A points configuration is attached, every attached one exists, and each is in order | `results` |
| Inkscape is installed, every template a switched-on output draws is valid, every colour slot a drawing uses has a colour, and the driver-photo setting could fetch something | `images` |

Anything wrong is listed with the command that fixes it, and no button is offered. Put it right and review again.

Where nothing is wrong, the review ends by asking whether you confirm the configuration, with a **Confirm** button beneath it. It is governed exactly as the placements button in step 12 is — who may press it, the five minutes, and the refusal if anything changed since the report.

When it goes through, the season moves on:

- **With the signup module on**, to **Waiting** — its signup window is yours to open next, in step 7.
- **With the signup module off, or test mode on**, straight to **Placements** — skip to step 8.

> **Every one of these checks runs again when you confirm placements.** Only the signup settings and the team list are fixed at this point, so a points configuration or a template can still go wrong in between — and the placements review will say so.

---

## Step 7 — Take signups

```
/signup open
```

Skip this step if the signup module is off. Opening the window moves the season to **Signups**; closing it — with `/signup close`, or at the close time you set — moves it to **Placements**. Everything in between is the signup module's job, and [its guide](configuring-the-signup-module.md) covers it.

---

## Step 8 — Build the season

The season is now **in placements**. This is when the calendar is built, because it is only now that you know how many drivers signed up and how many divisions they fill.

### Add your divisions

```
/division add name:Pro role:@Pro Division tier:1
/division add name:Academy role:@Academy tier:2
```

All three are required. Tiers must be unique, and by the time you confirm placements they must run 1, 2, 3… with no gaps — so if you delete your tier 2 division, something has to become tier 2.

If two divisions race the same calendar at different times, build the first one fully and then:

```
/division duplicate source_name:Pro new_name:Academy role:@Academy tier:2 hour_offset:2
```

That copies every round across and shifts each by the offset you give. Far quicker than typing a second calendar. There are two offsets — `day_offset` and `hour_offset` — and you can use either or both. Both may be negative; only `hour_offset` accepts a decimal.

Got something wrong? Until placements are confirmed you can fix any of it:

| Command | Use it to |
|---|---|
| `/division amend` | Change a division's name, tier or role — one, two or all three at once |
| `/division rename` | Change only the name |
| `/division delete` | Remove a division and all its rounds |

> **`/division add` is refused outside placements.** A season still in configuration, or waiting on or taking its signups, has no divisions yet — confirm the configuration and close the window first.

### Add the rounds

```
/round add division_name:Pro format:NORMAL scheduled_at:2026-03-08T20:00:00 track:12
```

Four things to know:

- **Times are UTC**, always, in the form `YYYY-MM-DDTHH:MM:SS`. When the bot posts a time to your drivers it converts it to each person's own local time, so put in the real UTC time and let it do that.
- **You never give a round number.** The bot sorts the division's rounds by date and numbers them. Add a round in the middle later and everything after it renumbers itself.
- **Four formats**: `NORMAL`, `SPRINT`, `MYSTERY` and `ENDURANCE`. You type the format rather than picking it from a list — case does not matter, but a name that is not one of the four is refused. Only `track` offers autocomplete.
- **A `MYSTERY` round takes no track**, and every other format must have one. That is the whole point of a mystery round: the circuit is kept secret until the weekend.

`/round delete` removes one while the season is in placements, and renumbers what remains.

> **Two rounds in the same division cannot share a date and time.** The command refuses the second one and names the round already sitting there. Confirming placements refuses such a season too, so this only saves you finding out later — duplicating a division with an offset of zero is the usual way it happens.

### Or paste the whole calendar at once

A full season is twenty-odd rounds per division, and adding them one command at a time is slow. Two commands take the lot in one go.

**`/round add-bulk`** takes a division name and opens a box. One round per line, times in UTC:

```
2026-03-08T20:00, Normal, 12
2026-03-15T20:00, Sprint, Hungaroring
2026-03-22T20:00, Mystery
```

Same rules as `/round add` — the track can be an ID, a name, or the `12 – Silverstone Circuit` form, and a `MYSTERY` round can leave it out. The box holds 2000 characters, which is a full season and more if you use track IDs, and rather less if you write the circuit names out in full.

**`/round add-xml`** takes no parameters and covers several divisions at once. Its one difference is worth the trouble: each round states its **local** time and the zone it is in, and the bot works out UTC for you.

```xml
<config>
  <division name="Pro">
    <round>
      <datetime>2026-03-08T20:00</datetime>
      <timezone>Europe/Lisbon</timezone>
      <format>Normal</format>
      <track>12</track>
    </round>
  </division>
</config>
```

Zone names are the IANA ones — `Europe/Lisbon`, `America/Sao_Paulo` — and must be spelled exactly, capitals included. Rounds can be in any order. About 23 rounds fit in the box, so a long season goes in over two runs.

Three things to know about both:

- **They add, never replace.** Running one twice does not wipe the first batch, which is what makes splitting a long calendar across runs safe.
- **One bad line rejects the whole thing.** Nothing is added and every problem is listed at once, with the line number. Fix the text and paste it again — that is the intended way to use them, and it is why nothing is half-applied.
- **`add-bulk` sidesteps daylight saving entirely** by taking UTC. If a round of yours falls near the weekend the clocks change, that is the safer command: a local time in the hour a clock skips forward does not exist, and the bot will take it at face value rather than querying it.

---

## Step 9 — Point each division at its channels

The bot posts nothing to a channel you have not named. Two channels are yours to set whatever else you use:

```
/division calendar-channel name:Pro channel:#pro-calendar
/division lineup-channel name:Pro channel:#pro-lineup
```

They are set **per division**, so a league with three divisions sets three of each.

The other six belong to modules. Set the ones whose module you turned on in step 2:

| Command | Needs this module | Carries |
|---|---|---|
| `/division weather-channel` | `weather` | Forecasts, and notices about cancelled rounds |
| `/division results-channel` | `results` | Session results |
| `/division standings-channel` | `results` | The championship tables |
| `/division verdicts-channel` | `results` | Penalty and appeal verdicts |
| `/division rsvp-channel` | `attendance` | Check-in calls |
| `/division attendance-channel` | `attendance` | The attendance sheet |

**Confirming placements will refuse a season that is missing any of these** — the calendar and lineup channels for every division, and the six for every enabled module — so it is cheaper to do them all now than to discover it at step 12.

> **A channel can be repointed at any stage.** If one is deleted mid-season, run the same command with its replacement; nothing about the season has to be undone.

> **A channel does one job.** Every one of these commands refuses a channel that is already set as something else — including your `/bot-init` command and log channels, your signup channel, and the same kind of channel in another division. A two-division league therefore needs its own results channel for each, its own calendar channel for each, and so on.
>
> It is not tidiness. Several of these postings **replace** the message they put up last, finding it by an id they store against the channel — so two purposes sharing a channel is how a lineup comes to delete a standings table.
>
> Setting a channel to the value it already has is refused too, but says so plainly rather than reporting a clash: nothing else holds it, and nothing is changed.

---

## Step 10 — Place your drivers

```
/driver assign user:@Alice division:1 team:Ferrari
/driver unassign user:@Alice division:1
/driver reject user:@Bob
```

Every driver the signup window let through is **Unassigned**, and each of them has to be **settled** before placements can be confirmed: placed in a team with `/driver assign`, or turned down with `/driver reject`. A signup still awaiting approval, or sent back for a correction, is unsettled too — finish reviewing it. [The signup guide](configuring-the-signup-module.md) covers reading the queue and its seeds.

**A placement is not live until it is confirmed.** Nothing is granted and nothing is posted when you assign: the division and team roles are handed out, and every lineup posted, when you confirm placements in step 12. Until then, `/driver unassign` takes a placement back without trace.

A driver may hold one seat per division. A team runs out of seats; the Reserve team always has room.

With test mode on, fake drivers are placed by `/test-mode roster add` instead, and `/driver assign` is refused for real ones — see [Testing with test mode](test-mode.md).

---

## Step 11 — Review the placements

```
/season placements-review
```

The bot posts the whole season to the channel, **as several messages rather than one**:

1. The season, and which modules are on.
2. Signup settings.
3. Attendance settings.
4. Points configurations.
5. Weather deadlines.
6. Image outputs — which of the eight are on, and what is wrong with any that are not.

Then a block per division giving its role, its channels, its full calendar and its lineup.

With the image module on and the calendar or lineup output switched on, that division's calendar and lineup arrive as the drawn pictures rather than as text, so what you approve is what your league will actually receive. See [Configuring the image module](configuring-the-image-module.md) for the switches. A picture that cannot be drawn takes the **Approve** button away until you fix it. Two settings do the same without any picture being wrong: a driver-photo setting that could never fetch anything, and — if you have turned per-tier colours on — a colour slot one of your drawings uses that a division has no colour for. The review says which of the three it is.

A module you have not switched on has no settings to show, so its message is simply not posted — six is the most you will see, not the number you should expect.

Read it properly. It is the last look you get at the season as a whole before it goes live, and it names anything that will stop confirmation.

> **A calendar with dates behind you takes the button away.** Two things can be wrong with a season's dates, and each division's calendar in the review carries its own:
>
> - **A round that has already run.** It would never open its result submission, so it could never take results at all. This applies **whatever modules you run** — a league with neither weather nor attendance has no windows to miss, but it has this one.
> - **A round already inside one of its windows** — its check-in call or a weather phase deadline fell due before you ran the review. See [Configuring the attendance module](configuring-the-attendance-module.md) and [Configuring the weather module](configuring-the-weather-module.md) for the timings this applies to.
>
> Only the **latest** round of each kind is named, beside that division's calendar. Every earlier round is implied by it: put that round right and you have put all of them right. Move the round with `/round amend`, or for a window shorten the window instead, then review again. Either way you get no **Approve** button until it is settled.
>
> The confirmation checks it a second time, because the review stands for five minutes and a round can cross a window while it is sitting there. So you can also meet this as a refusal on the button itself, having seen nothing wrong in the report.

One warning it raises that is easy to skim past: **"Reserve team has no role assigned"** — go back to step 3.

**And every signup must be settled.** A driver still Unassigned, awaiting approval or correcting their signup takes the button away, and the review names each of them — go back to step 10.

---

## Step 12 — Confirm the placements

The review ends by asking whether you accept the season, with a **✅ Approve** button beneath it. Press it. **There is no `/season approve` command** — approving commits your season, and the review is the evidence it is committed on, so the two are deliberately one action.

**You can press it if you ran the review, or if you hold the league admin role.** Anybody else who presses is told privately that they cannot, and nothing is approved. Running the review needs only the interaction role, so you may well be able to review a season you cannot approve — that is why the question is posted where everyone can see it rather than to you alone. Show it to a league admin and they can answer it from the same message.

> **The button stands for five minutes, and only for the season it was posted for.** When they pass, the message is deleted and replaced by one mentioning you to say the review has expired — run `/season placements-review` again. If the bot restarts while a review is waiting, the same thing happens as soon as it comes back up, because the five minutes cannot have run while it was off.
>
> Before it expires, the button still refuses if anything has changed since the report was drawn up — and it tells you what: the rounds, the channels, the seated drivers, even a drawing file edited on the bot's computer. Nothing is approved, and the question is cleared away just as an expiry clears it.
>
> The rule is simply that **what you read is what you approve**. A report describing a season you have since changed is not something anyone can approve from, so it stops being offered.

**The review disappears once you approve it.** The whole report goes, pictures and all — it described a season waiting on your decision, and you have made it. What you are told about the approval itself is private to you and stays, so you still see whether anything needed your attention. A review that expired is cleared the same way.

**These will refuse the season whatever modules you use:**

| The refusal | The fix |
|---|---|
| Division tiers are not 1, 2, 3… with no gaps | `/division amend` the tiers |
| A division has no rounds at all | Add one, or delete the division |
| Two rounds in a division share a date and time | Reschedule one |
| A team name cannot be used | Rename it — the message names every offender at once |
| A division has no calendar channel or no lineup channel | Step 9 |
| A signup is unsettled | Step 10 |

**And more, depending on what you turned on** — a missing channel for any enabled module, and every check from step 6 made again: a missing or badly ordered points configuration, incomplete signup settings, an unusable image template. Each is named individually with the command that fixes it. `/season placements-review` shows you all of them before you get here, and withholds the Approve button rather than offering you one that would be refused.

When it goes through, the bot:

1. **Locks in the calendar** and schedules every job the season needs — forecasts, result collection, check-in calls.
2. **Confirms every placement** and grants division and team roles to every placed driver.
3. **Posts the lineup** to each division's lineup channel.
4. **Posts the calendar** to each division's calendar channel.
5. **Posts the opening classification** to each division's standings and attendance channels — every driver and team on zero, with the season's rounds drawn empty beside them.

Your season is now **ongoing**.

> **The opening classification needs the results and attendance modules, not the images module.**
> Steps 3 and 4 draw nothing without `/images`; step 5 posts either way — as a drawing where the
> `standings` and `attendance` outputs are switched on, and as the ordinary text tables where they
> are not. It goes to the channels those modules already use, so a division with no standings
> channel simply gets nothing. Nothing here can stop a season being approved: a division whose
> sheets will not post is named in the log channel and the rest carry on.

---

## Step 13 — Running the season

```
/season status
```

Shows where each division has got to and what its next round is. This one only needs the interaction role, so you can let more people run it.

Things change. While the season is ongoing:

| Command | What it does |
|---|---|
| `/round amend` | Change a round's track, time or format — any combination of them, judged and applied as one change. Changing the time renumbers the division's rounds, and the new time must still be ahead — a round is never moved into the past. Forecasts are thrown away only where the round has moved far enough that they would not have been drawn yet; the confirmation tells you which before you commit |
| `/round cancel` | Call off one round. Needs `CONFIRM`, and posts a notice to the division. Refused once the round's results have been entered — the drivers' reports and appeals depend on them |
| `/division cancel` | Call off a whole division. Needs `CONFIRM`. Every round of it you have not yet raced is cancelled with it; rounds you have raced keep their results |
| `/division calendar-sync` | Repost a division's calendar with your changes on it |
| `/clean-bot` | Delete the bot's own most recent messages in the command channel. You say how many, up to ten, and nobody else's messages are touched. An approved or expired review clears itself, so this is for whatever else the bot has left behind |

> **The posted calendar does not update itself.** It is the calendar the season was approved with, and it stays that way. `/round amend` changes what the bot *does*, but the picture or the message your drivers scroll back to is untouched until you run `/division calendar-sync`. This trips up nearly everyone once.

### Changing the lineup

| Command | What it does |
|---|---|
| `/driver move` | Move a driver from their seat to another team, in the same division or another — one change, roles and lineups included |
| `/driver release` | Take a driver out of one division while they keep their other seats |
| `/driver sack` | Remove a driver from the season altogether. A league admin's |

Each takes effect at once, roles and lineup posts included, because the placement it changes is already confirmed.

### Signing up drivers mid-season

A signup window can be opened again while the season is ongoing. The same two steps follow it: closing the window moves the season to **Ongoing, placements** where anyone is left to settle — place or reject them as in step 10, then run `/season placements-review`, which in this stage reviews only the lineups and the drivers to confirm. Confirming grants the new drivers their roles, posts each lineup that changed once, and returns the season to ongoing. No division or round can be added mid-season.

Where the window closes with nobody left to settle, the season goes straight back to ongoing.

---

## Step 14 — Ending a season

```
/season complete
```

**Nothing ends a season by itself.** Once every division is finished or cancelled, the season moves to **pending completion** on its own, and `/season complete` is what ends it. Before then the bot refuses, listing the outstanding rounds; it refuses too while a mid-season signup window is open or its drivers are being placed. From pending completion no module can be turned off, and the last round's results can still be amended. Completing then ends the season: each division's **final classification** is posted, a history entry is written for every driver whose placement was confirmed, the season's roles are revoked, and every driver returns to **Not Signed Up** — ready to sign up for the next season. Drivers who never raced are deleted at this point, their signups kept with the season; former drivers are kept. An open signup window is closed, test mode is switched off, and the season is archived and announced in the log channel.

> **What "finalised" means here.** A round is finished once its **appeals review is approved** —
> not when you submit its results, and not when you approve its penalties. Each stage in between
> has its own name, and the refusal tells you which one a round is sitting in: *awaiting results*
> if nobody has entered them, *awaiting report verdicts* if they are posted and the reports are
> being judged, *awaiting appeal verdicts* if those are done and the appeals are not. Any of them
> counts as outstanding, which is usually the answer when the refusal names a round you thought was
> done: go back to its submission channel and finish the review it names. A cancelled round does
> not hold anything up. A division is finished once every one of its rounds is, and the season
> completes once every division is finished or cancelled.

> **If you do not run the results module**, a round becomes final as its time passes — there are no
> results to wait for — so your seasons complete without any of this.

> **The final classification is the mirror of the opening one.** Each division's standings channel
> gets its final standings and its attendance channel a final sheet, both headed `Final
> Classification` and holding the state at that division's last round with results. A division that
> ran no round gets none. The final attendance sheet is posted **beside** the last round's rather
> than replacing it, so the channel ends the season holding both — it is the season's last word and
> nothing should be able to delete it. As at approval, a division whose sheets will not post is
> named in the log channel and the season completes regardless.

An archived season cannot be edited. Start the next one with `/season setup` and a new game edition; your team list, your modules and your `/bot-init` settings all carry over.

**You cannot build next season while this one is still live.** A server holds one live season at a time, whatever its stage, so the season has to be completed, cancelled or aborted before `/season setup` will start another. Archived seasons are not live and never get in the way: every completed and cancelled season you have ever run stays in the database, with its rounds, results, standings and driver histories intact, and the stats commands keep reading them.

If you need to stop a season rather than finish it, which command depends on whether its placements were ever confirmed.

```
/season cancel confirm:CONFIRM
```

> ⚠️ **`/season cancel` is irreversible, and it is not `/season complete` with a different name.**
> It cascades: every division is cancelled, and with each one every round you have not yet raced.
> Rounds you *have* raced keep their results — cancelling never throws a result away — and the
> season is archived rather than deleted. Every driver whose placement was confirmed still gets a
> history entry — a cancelled season is league history, marked as cancelled so it can be told apart
> from one that ran to its end — and then, as on completion, every driver returns to Not Signed Up
> and those who never raced are deleted. What you lose is the ending: no final classification is
> posted. It is available only while the season is ongoing.

A season whose placements were never confirmed — one in configuration, waiting, signups or placements — is abandoned instead:

```
/season abort confirm:CONFIRM
```

> ⚠️ **`/season abort` keeps nothing.** The season is deleted with its divisions, rounds and every
> signup made for it, and it leaves no history — it never raced. Every driver returns to Not Signed
> Up and those who never raced are deleted; an open signup window is closed and test mode is
> switched off. It is a league admin's, and it frees the server for a new `/season setup` at once.

---

## Starting over

```
/bot-reset confirm:CONFIRM
```

Deletes every season, division, round and result, and keeps your `/bot-init` settings and team list so the bot stays usable straight away. Add `full:True` to wipe the `/bot-init` configuration too, in which case you start again from step 1 — but **not** from step 3: your team list survives either way, and there is no command that deletes it wholesale.

---

## Checklist before you confirm placements

- [ ] The bot is running, and its role sits above every role it must grant
- [ ] `/bot-init` has been run, both roles are set, and the log channel is one your drivers cannot read
- [ ] Every module you want is on — none can be turned on once placements are confirmed
- [ ] Every team is on the list, each with a role, and the Reserve team has one too
- [ ] Division tiers run 1, 2, 3… with no gaps
- [ ] Every division has at least one round, and no two rounds in it share a time
- [ ] Every round time is in UTC
- [ ] Mystery rounds have no track; every other round has one
- [ ] Every division has a calendar channel and a lineup channel
- [ ] Every division has the channels each enabled module needs
- [ ] Every signup is settled — each driver placed or rejected
- [ ] `/season placements-review` reports nothing blocking

---

## If something looks wrong

| What you see | Usually means |
|---|---|
| A command refuses with a short message only you can see | You are not in the command channel, or you hold neither of the two roles. Check the channel first — it is almost always the channel |
| A command does not appear in Discord's menu at all | The command list has not reached your server yet. Whoever hosts the bot can push it through immediately with `!sync` |
| "This command is a league admin's" | It asks for the league admin role, which you do not hold. The table at the top of this guide lists which commands those are; someone holding that role has to run them |
| "No league admin role is configured" | Your league was set up before the bot had one. A server administrator can put that right from any channel with `/bot-admin-role`, and every league admin command works again |
| The bot placed a driver but the role did not appear | The bot's own role sits below the role it is trying to grant. Move it up |
| `/division add` or `/season placements-review` refused, naming placements | The season is not in placements yet. Confirm its configuration with `/season config-review`, and close its signup window if it has one |
| `/team add` or a signup setting refused, naming the season | The season's configuration is confirmed, which fixes them until it ends |
| Confirmation refuses over tiers | A division was deleted and left a gap. `/division amend` something into it |
| "Unknown track" | The circuit name has to match exactly. Use the ID instead, or `/track list` to see the spellings |
| A round appears at the wrong time to your drivers | You entered local time, not UTC. `/round amend` it |
| The calendar in the channel is out of date | It only changes when you run `/division calendar-sync` |
| A division posts nothing where the others post fine | That division is missing the channel for it. Check step 9 |
| The season will not complete | Some round has not had its appeals review approved. The refusal names them — approve the appeals in each round's submission channel, or cancel a round that will never be raced. If it names a signup window instead, close it and confirm its placements first |
| Nothing at all is happening on schedule | The bot is not running. Starting it again picks up missed weather phases, missed check-in deadlines and a signup auto-close timer; anything else that came due while it was down is missed |

Anything the bot works out, fails to find, or falls back on is written to the log channel. When something is behaving oddly and this table has not explained it, read that channel — the answer is nearly always sitting in it.
