# Setting up the bot for your league

Everything the bot does sits on top of one thing: a **season**, divided into **divisions**, each running a list of **rounds**. This guide is the **order to do things in**, from an invited bot to an approved season racing on Sunday.

It covers only the parts every league needs, whichever features you use. The five optional modules — signup, results & standings, attendance, weather and images — each have their own settings, and this guide tells you when to turn one on, not how to configure it.

It sends you to the reference for the fine print:

- **[The main README](../../README.md)** — every command in full, with its parameters and who may run it.
- **[Configuring the weather module](configuring-the-weather-module.md)** — if you want generated forecasts before each round.
- **[Configuring the image module](configuring-the-image-module.md)** — if you want the bot posting pictures rather than text.

You do not need to read those first. Start here.

---

## A note on some words

**Division** — one championship within your league, with its own drivers, its own calendar and its own Discord role. A one-division league is perfectly normal; the bot simply has one of them.

**Tier** — where a division sits in the pecking order. Tier 1 is your top division. Every division needs one, they must all be different, and across the season they must run **1, 2, 3…** with no gaps. Tier is not the same as the division's name: you might call tier 1 "Pro" and tier 2 "Academy".

**Round** — one race weekend in a division's calendar. You never type a round number; the bot works them out by sorting your rounds by date.

**Setup** and **active** — a season is in *setup* while you are building it, and *active* once you approve it. This matters more than anything else in this guide, because it decides which commands will even run. Rounds and divisions can be freely added and deleted during setup; once a season is active you can only amend and cancel.

---

## Before you start

**Somebody has to run the bot on a computer.** It is not a service you invite and forget — it is a program that must stay running for forecasts to post and results to be collected. If it is switched off, nothing happens; nothing queues up and catches up later, except where this guide says so.

Three things have to be done on that computer, by hand, before any command in this guide works. They are covered in [Setup](../../README.md#setup) in the main README:

1. **Install it and give it your bot token** — the token comes from Discord's developer portal and goes in a file called `.env`.
2. **Turn on two switches in that same portal** — the *Server Members* and *Message Content* intents. Without the first the bot cannot hand out roles at all. See [Privileged Gateway Intents](../../README.md#privileged-gateway-intents).
3. **Start it once.** It builds its own database on first run. There is nothing to create.

**Invite it with the right permissions.** All of them listed under [Required Permissions](../../README.md#required-permissions) are genuinely used, and the two worth checking twice are **Manage Roles** — the bot cannot place a driver without it — and **Mention @everyone, @here, and All Roles**, which it needs to ping a division role even though it never pings everyone.

> **The bot's own role must sit above the roles it hands out.** Discord will not let it grant a role positioned above its own, whatever permissions it has. Drag the bot's role up your server's role list before you start assigning drivers.

If somebody else hosts the bot for you, you need their help for those three steps and nothing else. Everything below you do yourself, from Discord.

---

## Step 1 — Tell the bot who is in charge

```
/bot-init interaction_role:@Stewards interaction_channel:#bot-commands log_channel:#bot-logs
```

Three decisions, and they shape everything after:

| What you set | What it means |
|---|---|
| **Interaction role** | The role a person must hold to command the bot at all |
| **Interaction channel** | The **only** channel the bot accepts commands in |
| **Log channel** | Where the bot explains itself — what it worked out, what it could not find, why something fell back |

Run it anywhere; it is the one command that does not require the command channel, because until it has run there is no command channel.

**Pick a private channel for logs.** The bot writes a great deal there — every command that succeeded, every picture it could not draw, every driver it could not place. It is written for you, not for your drivers.

**Two levels of permission.** The interaction role gets you in the door. Beyond it:

| To run | You need |
|---|---|
| Most commands in this guide | The interaction role **and** Discord's **Manage Server** permission |
| `/module enable` and `/module disable` | Discord's **Administrator** permission |
| `/bot-init` and `/bot-reset` | **Manage Server**, from any channel |

So the interaction role is not a licence to reconfigure the league — it is the gate everything else sits behind. Drivers do not need it.

Run `/bot-init` again with `force:True` to change any of the three later.

> **A command run in the wrong channel is refused, not ignored.** You get a short message only you can see. If a command seems to do nothing, check which channel you are in first.

**One team already exists.** The first `/bot-init` creates the **Reserve** team, which has unlimited seats and belongs to every division. You cannot remove or rename it. Nothing else is created — your team list starts empty apart from it.

---

## Step 2 — Turn on the modules you want

```
/module enable results
```

Five modules, **all off to begin with**. The bot works without any of them, but a bare bot only holds a calendar.

| Module | What it adds |
|---|---|
| `signup` | A sign-up process drivers work through themselves, in their own private channel |
| `results` | Result submission, points, standings, penalties and appeals |
| `attendance` | Check-in calls before a race, attendance records, and penalties for missing one |
| `weather` | Generated forecasts published in three phases before each round — see [its own guide](configuring-the-weather-module.md) |
| `images` | Posts pictures instead of text — see [its own guide](configuring-the-image-module.md) |

**Order matters, and so does timing:**

| Module | The rule |
|---|---|
| `attendance` | Turn `results` on first. Attendance is refused without it |
| `results` and `attendance` | Cannot be turned on **or off** while a season is active. Decide before you approve |
| `weather` | Can be turned on mid-season, but every division must already have a forecast channel. It then immediately runs any forecast that is already overdue |
| `signup` and `images` | Free to switch at any time |

**Turning a module off clears its settings** — its channels and roles are forgotten, and its scheduled jobs are cancelled. Your history is always kept, but you will be setting the module up again from scratch if you turn it back on. The images module is the exception: it forgets nothing and comes back exactly as it was.

Each module then has its own configuration, which is not covered here. Start from [Slash Commands](../../README.md#slash-commands) in the README and find that module's section.

> **Decide now, not later.** Two of the five cannot be changed once a season is running, and a third changes what `/season approve` demands of you. This step being early is not an accident.

---

## Step 3 — Build your team list

```
/team add name:Red Bull role:@Red Bull
/team list
```

A team belongs to the **server**, not to a season, so you do this once and it carries forward. Every team needs a Discord role — that role is granted to a driver when you seat them and taken away when you do not.

The rules for a team's **name** are stricter than you would expect, and they are checked the moment you set it. They are listed in full under [`/team add`](../../README.md#team-commands). The short version: a name has to start with a letter and has to stay distinct from every other team once punctuation and accents are stripped, so `Red Bull` and `Red  Bull!` cannot both exist.

> **These rules apply whether or not you ever use pictures.** A name is only cheap to fix at the moment you set it, so the bot constrains it then, rather than leaving you stuck with a name you cannot correct without losing that team's history.

**Set the Reserve team's role too:**

```
/team reserve-role role:@Reserve
```

This is easy to forget and the bot warns you about it at every season review, because a driver sitting in Reserve without it will be rejected when results are submitted.

Adding a team while a season is in setup also seats it in every division of that season, with **two seats** each. Use `/team list` to see the whole list with its roles, and `/team lineup` once drivers are placed.

---

## Step 4 — Look at the circuit list

```
/track list
```

The bot carries **28 circuits**, each with an ID and a full name — `12` is Silverstone Circuit, `22` is Autódromo José Carlos Pace. You use either when adding a round, and autocomplete offers them as you type.

**The list is fixed.** You cannot add a circuit of your own, rename one, or change how wet the bot thinks it is. Each circuit's rainfall behaviour ships with the bot and is the same on every server.

Worth a look now rather than mid-way through building a calendar, because the names are the official circuit names rather than country names, and they are what a round is stored under. The full list is also in the README under [Track ID Reference](../../README.md#track-id-reference).

---

## Step 5 — Build the season

```
/season setup game_edition:25
```

The game edition is the year of the F1 game you are racing on — `25` for F1 25. The bot numbers the season for you.

You are now **in setup**. Nothing you do from here is live until step 8.

### Add your divisions

```
/division add name:Pro role:@Pro Division tier:1
/division add name:Academy role:@Academy tier:2
```

All three are required. Tiers must be unique, and by the time you approve they must run 1, 2, 3… with no gaps — so if you delete your tier 2 division, something has to become tier 2.

If two divisions race the same calendar at different times, build the first one fully and then:

```
/division duplicate source_name:Pro new_name:Academy role:@Academy tier:2 hour_offset:2
```

That copies every round across and shifts each by the offset you give. Far quicker than typing a second calendar, and offsets can be negative or fractional.

Got something wrong? During setup you can fix any of it:

| Command | Use it to |
|---|---|
| `/division amend` | Change a division's name, tier or role — one, two or all three at once |
| `/division rename` | Change only the name |
| `/division delete` | Remove a division and all its rounds |

### Add the rounds

```
/round add division_name:Pro format:NORMAL scheduled_at:2026-03-08T20:00:00 track:12
```

Four things to know:

- **Times are UTC**, always, in the form `YYYY-MM-DDTHH:MM:SS`. When the bot posts a time to your drivers it converts it to each person's own local time, so put in the real UTC time and let it do that.
- **You never give a round number.** The bot sorts the division's rounds by date and numbers them. Add a round in the middle later and everything after it renumbers itself.
- **Four formats**: `NORMAL`, `SPRINT`, `MYSTERY` and `ENDURANCE`.
- **A `MYSTERY` round takes no track**, and every other format must have one. That is the whole point of a mystery round: the circuit is kept secret until the weekend.

`/round delete` removes one during setup, and renumbers what remains.

> **Two rounds in the same division cannot share a date and time.** You can add them, but approval will refuse the season and name the clash. Duplicating a division with an offset of zero is the usual way this happens.

---

## Step 6 — Point each division at its channels

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

**Approval will refuse a season that is missing any of these for an enabled module**, so it is cheaper to do them all now than to discover it at step 8.

---

## Step 7 — Review what you have built

```
/season review
```

The bot posts the whole configuration to the channel: which modules are on, the settings each of them holds, your points configurations, and then a block per division giving its role, its channels, its full calendar and its lineup.

Read it properly. It is the last look you get at the season as a whole before it goes live, and it names anything that will stop approval.

Two warnings it raises that are easy to skim past:

- **"Reserve team has no role assigned"** — go back to step 3.
- **"*n* driver(s) UNASSIGNED"** — drivers exist who are in no team. Placing drivers belongs to the signup module; see [Driver Commands](../../README.md#driver-commands).

There is an **Approve** button on the review itself, which does exactly what the next step does.

---

## Step 8 — Approve

```
/season approve
```

**Four things will refuse the season whatever modules you use:**

| The refusal | The fix |
|---|---|
| Division tiers are not 1, 2, 3… with no gaps | `/division amend` the tiers |
| A division has no rounds at all | Add one, or delete the division |
| Two rounds in a division share a date and time | Reschedule one |
| A team name cannot be used | Rename it — the message names every offender at once |

**And more, depending on what you turned on** — a missing channel for any enabled module, a missing or badly ordered points configuration, incomplete signup settings, an unusable image template. Each is named individually with the command that fixes it. `/season review` shows you all of them before you get here.

When it goes through, the bot:

1. **Locks in the calendar** and schedules every job the season needs — forecasts, result collection, check-in calls.
2. **Grants division and team roles** to every placed driver.
3. **Posts the lineup** to each division's lineup channel.
4. **Posts the calendar** to each division's calendar channel.

Your season is now **active**.

---

## Step 9 — Running the season

```
/season status
```

Shows where each division has got to and what its next round is. This one only needs the interaction role, so you can let more people run it.

Things change. During an active season:

| Command | What it does |
|---|---|
| `/round amend` | Change a round's track, time or format. Changing the time renumbers the division's rounds; changing the track or format throws away any forecast already generated for it |
| `/round cancel` | Call off one round. Needs `CONFIRM`, and posts a notice to the division |
| `/division cancel` | Call off a whole division. Needs `CONFIRM` |
| `/division calendar-sync` | Repost a division's calendar with your changes on it |
| `/clean-bot` | Delete the bot's own messages in the command channel — handy after a long review |

> **The posted calendar does not update itself.** It is the calendar the season was approved with, and it stays that way. `/round amend` changes what the bot *does*, but the picture or the message your drivers scroll back to is untouched until you run `/division calendar-sync`. This trips up nearly everyone once.

---

## Step 10 — Ending a season

```
/season complete
```

**Nothing ends a season by itself.** You run this once every round in every division has been finalised, and the bot refuses — listing the outstanding rounds — until they are. It then archives the season: it is marked complete, a history entry is written for every driver who raced, and it is announced in the log channel. **Nothing is deleted.**

An archived season cannot be edited. Start the next one with `/season setup` and a new game edition; your team list, your modules and your `/bot-init` settings all carry over.

If you need to abandon a season rather than finish it:

```
/season cancel confirm:CONFIRM
```

> ⚠️ **`/season cancel` destroys the season and every result in it, permanently.** It is not `/season complete` with a different name. Use it only for a season that should never have existed.

---

## Starting over

```
/bot-reset confirm:CONFIRM
```

Deletes every season, division, round and result, and keeps your `/bot-init` settings and team list so the bot stays usable straight away. Add `full:True` to wipe the configuration too, in which case you start again from step 1.

---

## Checklist before you approve

- [ ] The bot is running, and its role sits above every role it must grant
- [ ] `/bot-init` has been run, and the log channel is one your drivers cannot read
- [ ] Every module you want is on — including the two you cannot change later
- [ ] Every team is on the list, each with a role, and the Reserve team has one too
- [ ] Division tiers run 1, 2, 3… with no gaps
- [ ] Every division has at least one round, and no two rounds in it share a time
- [ ] Every round time is in UTC
- [ ] Mystery rounds have no track; every other round has one
- [ ] Every division has a calendar channel and a lineup channel
- [ ] Every division has the channels each enabled module needs
- [ ] `/season review` reports nothing blocking

---

## If something looks wrong

| What you see | Usually means |
|---|---|
| A command refuses with a short message only you can see | You are not in the command channel, or you lack the interaction role. Check the channel first — it is almost always the channel |
| A command does not appear in Discord's menu at all | The command list has not reached your server yet. Whoever hosts the bot can push it through immediately with `!sync` |
| "You need the Administrator permission" | Only `/module enable` and `/module disable` ask for that. Someone with it has to run them |
| The bot placed a driver but the role did not appear | The bot's own role sits below the role it is trying to grant. Move it up |
| Approval refuses over tiers | A division was deleted and left a gap. `/division amend` something into it |
| "Unknown track" | The circuit name has to match exactly. Use the ID instead, or `/track list` to see the spellings |
| A round appears at the wrong time to your drivers | You entered local time, not UTC. `/round amend` it |
| The calendar in the channel is out of date | It only changes when you run `/division calendar-sync` |
| A division posts nothing where the others post fine | That division is missing the channel for it. Check step 6 |
| The season will not complete | Some round is not finalised. The refusal names them |
| Nothing at all is happening on schedule | The bot is not running. Nothing catches up by itself except weather, and only when you re-enable the module |

Anything the bot works out, fails to find, or falls back on is written to the log channel. When something is behaving oddly and this table has not explained it, read that channel — the answer is nearly always sitting in it.
