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

**Principal division** — the highest tier division a driver holds a full-time seat in. A driver can race in more than one division at once — full-time in one, a reserve in others — and this is the one the rules name when only one will do. Somebody full-time in tier 2 who reserves in tier 1 has tier 2 as their principal division; somebody who only ever reserves, or who holds no seat, has none. You never set it. The bot works it out from the seats the driver holds, so it follows along when you place, move or release them.

**Base role** and **driver role** — the league's roles for its people, as against the interaction and league admin roles, which are for the people who run it. The base role is who your members are, and the driver role is who your drivers are right now. See [step 1](#step-1--tell-the-bot-who-is-in-charge).

**Hub** — the one channel every member of your league can use the bot from, rather than only the people running it. The bot keeps a panel of buttons there; see [step 1](#step-1--tell-the-bot-who-is-in-charge).

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

**Somebody has to run the bot on a computer.** It is not a service you invite and forget — it is a program that must stay running for forecasts to post and results to be collected. If it is switched off, nothing happens while it is down. Some of it is picked up when it starts again — missed weather phases, missed check-in calls whose deadline is still ahead, missed check-in deadlines, the forecasts and check-in messages due to be tidied away a day after a round, and a signup window's auto-close timer are all recovered, and so is a change the bot was part-way through, which is finished from the first step it had not done — but anything else that came due is simply missed.

Three things have to be done on that computer, by hand, before any command in this guide works. They are covered in [Setup](../../README.md#setup) in the main README:

1. **Install it and give it your bot token** — the token comes from Discord's developer portal and goes in a file called `.env`. The bot application is your league's alone: one bot serves one league.
2. **Set three switches in that same portal** — turn on the *Server Members* and *Message Content* intents, and turn off *Public Bot*. Without the first intent the bot cannot hand out roles at all. See [Privileged Gateway Intents](../../README.md#privileged-gateway-intents). With *Public Bot* off, nobody but the bot's owner can add it to a server.
3. **Start it once.** It builds its own databases on first run — two of them, `bot.db` for your league's records and `scheduler.db` for work it has scheduled ahead. There is nothing to create.

> **If you keep backups, keep both.** `bot.db` on its own is not a complete backup: without `scheduler.db` a restored season still knows its rounds, but every weather phase, RSVP notice and result submission it was waiting to send has gone. A season under way cannot be approved again, and no command rebuilds its scheduled work wholesale: `/round amend` arms one round's work again when it moves that round, and nothing else does. Copying `bot.db` while the bot is running can also miss the most recent changes, because they may still be sitting in a `bot.db-wal` file next to it. The safe ways are in [Backing up](../../README.md#backing-up). Backups are optional and entirely your choice — but a half-backup is worse than none, because it looks complete.

**Invite it to your league's server, and to no other.** `/bot init` claims the server it runs in. On any other server the bot refuses every command, and the computer running it logs a warning for as long as it sits in more than one — see [One bot, one server](../../README.md#one-bot-one-server).

**Invite it with the right permissions.** All of them listed under [Required Permissions](../../README.md#required-permissions) are genuinely used, and the two worth checking twice are **Manage Roles** — the bot cannot place a driver without it — and **Mention @everyone, @here, and All Roles**, which it needs to ping a division role, and the interaction role when a round's results are due, even though it never pings everyone.

> **The bot's own role must sit above the roles it hands out.** Discord will not let it grant a role positioned above its own, whatever permissions it has. Drag the bot's role up your server's role list before you start assigning drivers.

If somebody else hosts the bot for you, you need their help for those three steps and nothing else. Everything below you do yourself, from Discord.

---

## Step 1 — Tell the bot who is in charge

```
/bot init interaction_role:@Stewards league_admin_role:@Owners interaction_channel:#bot-commands log_channel:#bot-logs
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
| Anything that cannot be undone: `/bot pack`, `/clean-bot`, `/module enable` and `/module disable`, cancelling or deleting a season, division or round, aborting or completing a season, `/team remove`, `/driver sack`, and every `/test-mode` command | The **league admin role** |
| Confirming a season's configuration or its placements, on the button `/season config-review` or `/season placements-review` posts | Whoever ran that review, or the league admin role |
| `/bot init` and the four commands that change one setting | The league admin role **or** Discord's **Administrator** permission, from any channel |
| `/bot factory-reset` | The Discord **server owner**, and nobody else, from any channel |

The league admin role covers everything the interaction role does, so whoever holds it can
run everything in this guide without also being given the interaction role.

**Discord's own permissions do not come into it.** Administrator and Manage Server govern
your *server*; these two roles govern your *league*, and the bot reads only the roles. The
one exception is the five setup commands in the row above — they take Administrator too,
because they are what repairs the settings everything else depends on. `/bot factory-reset`
stands apart from both roles: it erases the league entire, so it is the server owner's alone.

So the interaction role is not a licence to reconfigure the league — it is the gate
everything else sits behind. Drivers do not need it.

**`/bot init` runs once.** Run it a second time and it politely refuses — it will not overwrite what is already there. To change one of the four settings afterwards, use the command for that setting:

```
/bot log-channel channel:#new-bot-logs
/bot interaction-channel channel:#new-bot-commands
/bot interaction-role role:@NewStewards
/bot admin-role role:@NewOwners
```

Each changes that one setting and touches nothing else.

> **These work from any channel, and a server administrator can run them.** That is on purpose. They exist for the day something goes wrong with the four settings themselves — somebody deletes the log channel, archives the command channel, or removes one of the two roles. If they needed the command channel or a league role to run, the one thing you could not repair would be the thing that had broken; and if they needed the league admin role, a server that had lost that role could never get it back. So these five, and only these five, also accept Discord's **Administrator** permission.
>
> `/bot pack` is **not** one of them. It clears the settings rather than repairing them, so it asks for the league admin role and is given in the command channel like everything else.

To move your league to another server, or to start over from nothing, see [Starting over](#starting-over).

> **A command run in the wrong channel is refused, not ignored.** You get a short message only you can see. If a command seems to do nothing, check which channel you are in first.

**Two more roles, the league's own.** The base role is who your league's members are, and the driver role is who its drivers are right now:

```
/bot base-role role:@Members
/bot driver-role role:@Driver
```

| Role | What it means |
|---|---|
| **Base role** | Who your league's members are. You hand it out — the bot only ever does when it replaces a deleted one, below. With signups on, it decides who can see the signup channel, and it is pinged when signups open |
| **Driver role** | Who your drivers are. The bot grants it when you approve a signup, and takes it back whenever a driver returns to Not Signed Up — turned down, sacked, or at the end of the season |

These are ordinary commands: the interaction role runs them, in the command channel. **Once a season's configuration is confirmed they are fixed until that season ends**, so set them first. Make them two different roles: one is who may sign up, the other is who got through.

**The bot must be able to grant the driver role.** Put the bot's own role above it in your server's role list, and give the bot **Manage Roles**. `/bot driver-role` refuses a role it cannot grant — `@everyone`, a role a bot or subscription manages, or one above its own — and says why.

> **A role deleted from the server can be replaced, even mid-season.** Nobody holds a deleted role, so the lock above does not apply to it: run `/bot base-role` or `/bot driver-role` with its replacement. The bot gives the new role to every current driver and names anyone it could not give it to. For the base role, give it to your other members yourself — the bot knows only its drivers. Until you do, signups cannot be opened: `/signup open` refuses while either role is gone, or while the bot cannot grant the driver role.

**A hub for your members, if you want one.** Every command here is for the people running the league. The hub is a channel for everybody else: the bot keeps a single panel of buttons there, and anyone who can see the channel can press them.

```
/bot hub-channel channel:#league-hub
```

It is visible to your base role — or to every member, if you have not set one — and to both league roles, and nobody but the bot can post there. Change any of those roles later and the hub follows. Give it a channel of its own: setting the hub replaces that channel's permissions. The panel starts with **About**, which tells whoever presses it which bot this is, which version it runs and when that version was made; modules add their options to it as they are built.

**One team already exists.** Your team list always holds the **Reserve** team, which has unlimited seats and belongs to every division. You cannot remove or rename it. Nothing else ships with the bot — your team list starts empty apart from it.

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

> **Turning `results` off takes `attendance` with it.** If attendance is on, the bot warns you what the cascade will take and disables nothing until you confirm; you are told at once that the change is under way, that message is then updated to name both modules, and the log channel records it as well.

> **And if a season is running, turning `results` off destroys that season's championship.** Every classification recorded, every standing computed from them, and every results and standings message already posted are deleted; every round still waiting on results, report verdicts or appeal verdicts is closed as having run without results, which is what lets you complete the season afterwards. Every penalty and appeal verdict already announced is removed from the verdicts channel with them; auto-sack and auto-reserve announcements stay. Your points configurations and your division channels are kept. You are warned and must confirm, and none of it can be undone. Once you have, the bot tells you at once that it is turning `results` off and updates that message with what went; if it takes longer, the log channel says so. If a removal fails, the queue stops there: see [When a job stops the queue](#when-a-job-stops-the-queue). See [Configuring the results module](configuring-the-results-module.md).

**Turning a module off clears less than you would expect.** The signup module forgets its channel, and keeps its time slots and its question settings; the base role and the driver role are the league's, and no module's switch touches them. Of the rest, `attendance` forgets only which channels each division posts to, and `weather`, `results` and `images` forget no *configuration* at all — every channel, deadline, penalty value and points configuration survives, and the module comes back as it was. What `results` does destroy, where a season is running, is that season's results themselves, which are not configuration; see the call-out above. What weather does do is cancel its own scheduled jobs — the forecasts for every remaining round. Turning a module off stops that module's work and nobody else's: the jobs belonging to the modules you left on go on running.

Each module then has its own configuration, which is not covered here. Start from [Slash Commands](../../README.md#slash-commands) in the README and find that module's section.

> **Decide now, not later.** Signup is fixed the moment a season's configuration is confirmed, and none of the other four can be turned *on* once its placements are. Turning `results` or `attendance` off mid-season does real and irreversible damage — `results` deletes the season's championship outright — and each module you turn on adds to what confirming a season demands of you. This step being early is not an accident.

---

## Step 3 — Build your team list

```
/team add
/team list
```

`/team add` opens a form. It asks for three things, and each does one job:

| | What it is for |
|---|---|
| **Shorthand** — `RBR` | What you **type**: commands, the team column of a results submission, a test roster. It is also the filename of the team's badge. At most 16 characters, and no comma. |
| **Full name** — `Oracle Red Bull Racing` | What is **shown**: every post, every graphic, every reply. At most 32 characters, and no two teams may share one. |
| **Role** — `@Red Bull` | Granted to a driver when you seat them, taken away when you do not. |

A team belongs to the **server**, not to a season, so you do this once and it carries forward. **One role, one team:** a role another team already holds, the Reserve team included, is refused. The role must also be one the bot can grant — not `@everyone`, not a role managed by an integration, and not one sitting above the bot's own highest role; each is refused as you choose it, saying why.

> **You never type a role to name a team.** Every command that takes a team suggests the shorthands as you type, showing the full name beside each, and a role mention typed in its place is refused.

The rules for a team's names are checked the moment you set them, and are listed in full under [the team commands](../../README.md#team-commands). The short version: a **shorthand** has to stay distinct from every other team's once punctuation and accents are stripped, so `Red Bull` and `Red  Bull!` cannot both exist — they would draw the same badge file. It may start with a digit; `2 Fast` is fine. Neither name may hold an emoji, Discord formatting, a role mention, a mention of a member, `@everyone` or `@here`: a picture cannot show them the way a message does.

> **These rules apply whether or not you ever use pictures.** A name is only cheap to fix at the moment you set it, so the bot constrains it then, rather than leaving you stuck with a name you cannot correct without losing that team's history.

**Set the Reserve team's role too:**

```
/team reserve-role role:@Reserve
```

This is easy to forget and the bot warns you about it at every season review, because a driver sitting in Reserve without it will be rejected when results are submitted.

Every division you create later is built from this list, each team with **two seats**, and keeps the names the team held then. Build the list before you confirm a season's configuration: from that moment until the season ends, `/team add` and `/team remove` are refused, and so are the two names in `/team modify`.

**`/team modify` is how a team changes.** Name the team by its shorthand and it opens a form filled with what the team holds now; change what you like and submit. The two names may change only while the list is open, as above. A team's role is the exception — if a role is deleted from the server, point the team at its replacement in any state but pending completion. (By then nothing is being raced and completing the season revokes every team role anyway, so the repair waits for the next season.) Every driver already seated in the team moves to the new role with it, and the rounds the team has already raced stay its own — a result records the team, never its role or its name. If you change the shorthand, the reply tells you the badge filename to rename.

Use `/team list` to see the whole list — each team as *full name — `shorthand` → @role* — and `/team lineup` once drivers are placed.

---

## Step 4 — Look at the circuit list

```
/track list
```

The bot carries **28 circuits**, each with an ID and a full name — `12` is Silverstone Circuit, `22` is Autódromo José Carlos Pace. You use either when adding a round, and autocomplete offers them as you type. Picking a suggestion is the easiest route, but you are not tied to it: the ID on its own, the name in any capitalisation, and the whole line as the dropdown shows it (`12 – Silverstone Circuit`) all work, so retyping or pasting an entry is safe. Where an ID and a name appear together, the ID decides.

**The list is fixed.** You cannot add a circuit of your own, rename one, or change how wet the bot thinks it is. Each circuit's rainfall behaviour ships with the bot and is the same for every league.

Worth a look now rather than mid-way through building a calendar, because the names are the official circuit names rather than country names, and they are what a round is stored under. The full list is also in the README under [Track ID Reference](../../README.md#track-id-reference).

---

## Step 5 — Start the season

```
/season setup game_edition:25
```

The game edition is the year of the F1 game you are racing on — `25` for F1 25. The bot numbers the season for you.

The season is now **in configuration**. Nothing you do from here is live until its placements are confirmed.

**This is the last moment steps 2 and 3 are open.** Configuration is where the season's settings are decided, and confirming it fixes them until the season ends:

- the **team list** — `/team add`, `/team remove`, and the two names in `/team modify`;
- the **signup module** — turning it on or off, and every setting in [its guide](configuring-the-signup-module.md);
- **test mode**, which can be switched on or off only now — see [Testing with test mode](test-mode.md);
- and the game edition you just gave.

The other modules' settings stay open after confirmation, but none of them can be turned **on** once placements are confirmed. If your league attaches points configurations to its seasons, attach them now — see [Configuring the results module](configuring-the-results-module.md).

---

## Step 6 — Confirm the configuration

```
/season config-review
```

The bot posts the configuration to the channel, a message per subject: test mode, which modules are on, the team list with its roles, and the settings of each module you turned on — signup, attendance, the points configurations attached, the weather deadlines and the image outputs — in the same words the placements review uses later. Then it checks everything that can be checked before the season has any divisions:

| The check | Only with |
|---|---|
| The signup channel, and the league's base role and driver role, are set; both roles are still on the server; the bot can grant the driver role | `signup` |
| Every team's shorthand can be used | — |
| A points configuration is attached, every attached one exists, and each is in order | `results` |
| Inkscape is installed, every template a switched-on output draws is valid, every colour slot a drawing uses has a colour, and the driver-photo setting could fetch something | `images` |

Anything wrong is listed with the command that fixes it, and no button is offered. Put it right and review again.

Where nothing is wrong, the review ends by asking whether you confirm the configuration, with a **Confirm** button beneath it. It is governed exactly as the placements button in step 12 is — who may press it, the five minutes, the refusal if anything changed since the report, and a press turned away for any other reason leaving the review up for you to put it right and press again.

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

**Keep the name plain.** A division's name heads everything the bot posts for it, text and pictures alike, so it cannot hold an emoji, Discord formatting such as `**bold**`, a role mention, a mention of a member, `@everyone` or `@here`. The same goes for every command that names or renames one.

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
| `/division delete` | Remove a division and all its rounds (unassign its drivers first: a division with a driver placed in it cannot be deleted) |

> **`/division add` is refused outside placements.** A season still in configuration, or waiting on or taking its signups, has no divisions yet — confirm the configuration and close the window first.

### Add the rounds

```
/round add division_name:Pro format:NORMAL scheduled_at:2026-03-08T20:00:00 track:12
```

Four things to know:

- **Times are UTC**, always, in the form `YYYY-MM-DDTHH:MM:SS`. Give one with a zone, such as `2026-06-14T20:00+02:00`, and the bot converts it to UTC for you. When the bot posts a time to your drivers it converts it to each person's own local time, so put in the real UTC time and let it do that.
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

The other six belong to modules, and each is set by its module's own command. Set the ones whose module you turned on in step 2:

| Command | Needs this module | Carries |
|---|---|---|
| `/weather channel` | `weather` | Forecasts, and a silent note when a round is cancelled |
| `/results channel results` | `results` | Session results |
| `/results channel standings` | `results` | The championship tables |
| `/results channel verdicts` | `results` | Penalty and appeal verdicts |
| `/attendance channel rsvp` | `attendance` | Check-in calls |
| `/attendance channel attendance` | `attendance` | The attendance sheet |

**Confirming placements will refuse a season that is missing any of these** — the calendar and lineup channels for every division, and the six for every enabled module — so it is cheaper to do them all now than to discover it at step 12. A channel set and since deleted from the server counts as missing, and confirming a mid-season window's placements checks them all again.

> **A channel can be repointed at any stage of the season.** If one is deleted mid-season, run the same command with its replacement; nothing about the season has to be undone. The channels belong to that season: once it ends they no longer count, and the next season's divisions are given their own.

> **A channel does one job.** Every one of these commands refuses a channel that is already set as something else — including your `/bot init` command and log channels, your signup channel, and the same kind of channel in another division. A two-division league therefore needs its own results channel for each, its own calendar channel for each, and so on.
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

With the image module on and the calendar or lineup output switched on, that division's calendar and lineup arrive as the drawn pictures rather than as text, so what you approve is what your league will actually receive. See [Configuring the image module](configuring-the-image-module.md) for the switches. A picture that cannot be drawn takes the **Approve** button away until you fix it. So does anything else the image module's own checks find, though the review draws only those two pictures: the program that draws them missing, a drawing for another output you have switched on that will not load, a lineup drawing too small for one of your divisions, a driver-photo setting that could never fetch anything, and — if you have turned per-tier colours on — a colour slot one of your drawings uses that a division has no colour for. The review names each.

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

**And every signup must be settled.** A driver still Unassigned, awaiting approval or correcting their signup takes the button away. The review lists each of them by name, in its public report, so that anyone reading it can see who is still waiting — go back to step 10.

---

## Step 12 — Confirm the placements

The review ends by asking whether you accept the season, with a **✅ Approve** button beneath it. Press it. **No command does it for you** — approving commits your season, and the review is the evidence it is committed on, so the two are deliberately one action.

**You can press it if you ran the review, or if you hold the league admin role.** Anybody else who presses is told privately that they cannot, and nothing is approved. Running the review needs only the interaction role, so you may well be able to review a season you cannot approve — that is why the question is posted where everyone can see it rather than to you alone. Show it to a league admin and they can answer it from the same message.

> **The button stands for five minutes from when the question appears, and only for the season it was posted for.** Pressing it — even a press that is turned away — does not buy more time. When they pass, the message is deleted and replaced by one mentioning you to say the review has expired — run `/season placements-review` again. If the bot restarts while a review is waiting, the same thing happens as soon as it comes back up, report and all, because the five minutes cannot have run while it was off.
>
> If your press is turned away — a date gone by, a channel missing, anything else the approval checks — or you cancel at the backup question, or it fails on a fault in the bot, the review and its button stay: put it right and press again until the five minutes are up. Only a press the bot takes in hand clears the review. Press once and wait: while your press is being worked the review will not expire under you, and a second press is turned away.
>
> Before it expires, the button still refuses if anything has changed since the report was drawn up — and it tells you what: the rounds, the channels, the seated drivers, the signups still waiting, test mode, the team list and its roles, even a drawing file edited on the bot's computer. Nothing is approved, and the question is cleared away just as an expiry clears it.
>
> The rule is simply that **what you read is what you approve**. A report describing a season you have since changed is not something anyone can approve from, so it stops being offered.

**The review disappears once you press Approve and the approval is taken in hand, and not before.** The whole report goes, pictures and all — it described a season waiting on your decision, and you have made it. If the approval is then refused when its turn comes on the queue, or a league admin discards it, your season stays in placements with the review gone: run `/season placements-review` again. What you are told about the approval itself is private to you and stays, so you still see whether anything needed your attention. A review that expired is cleared the same way.

**These will refuse the season whatever modules you use:**

| The refusal | The fix |
|---|---|
| The season has no divisions, or every one is cancelled | `/division add` one |
| Division tiers are not 1, 2, 3… with no gaps | `/division amend` the tiers |
| A division has no rounds at all | Add one, or delete the division |
| Two rounds in a division share a date and time | Reschedule one |
| A team's shorthand cannot be used | The team list is fixed once the configuration is confirmed, so only `/season abort` and configuring the season again clears it. `/season config-review` refuses such a shorthand, so a season rarely gets this far with one |
| A division is missing a channel it posts to — its calendar or lineup channel, or one an enabled module needs — or one has been deleted from the server | Step 9 — the message names the division, the channel and the command |
| A signup is unsettled | Step 10 |

**And more, depending on what you turned on** — every check from step 6 made again: a missing or badly ordered points configuration, incomplete signup settings, an unusable image template. The base role and the driver role are the exception: confirming the configuration fixed them, so they are not checked again. Each is named individually with the command that fixes it. `/season placements-review` shows you all of them before you get here, and withholds the Approve button rather than offering you one that would be refused.

The approval is acknowledged at once ("⏳ Approving season 3 …", naming its first job), the review is cleared, and the rest is carried out on the change queue, each step a job of its own (see [When a job stops the queue](#when-a-job-stops-the-queue)). The bot:

1. **Saves the season** in one go — the calendar's sessions, the confirmed placements, the points configurations and the season's state. Either all of it is saved or none of it is.
2. **Arms the season's timed work** — forecasts with weather on, every round's result collection whatever modules you run, and check-in calls with attendance on. It is done after the save, so a season that is not approved has nothing armed.
3. **Confirms every placement's roles**: division and team roles are granted to every placed driver, one job each.
4. **Posts the lineup** to each division's lineup channel.
5. **Posts the calendar** to each division's calendar channel.
6. **Posts the opening classification** to each division's standings channel, and the opening sheet to its attendance channel — every driver and team on zero, with the season's rounds drawn empty beside them.

Your season is **ongoing** from the moment it is saved in step 1, before anything is granted or posted. So a round you spot as wrong while the calendar is still arriving can be moved with `/round amend` straight away, and `/round add` is closed from then on.

**A season already being approved is not approved twice.** Pressing Approve on another review of the same season while the approval is waiting, running or stopped on a failure is refused at once, without naming the job, and is not asked the backup question.

**If one of steps 2 to 6 cannot be done, the approval still stands.** The job that failed stops the queue until it is retried or a league admin discards it. Your confirmation then lists each job a league admin discarded, under **Not everything could be done**, with timed work that was not armed first, for no command arms it again:

- the setup the bot held in memory, where it could not be let go of — `/round amend` may refuse this season until the bot restarts;
- a driver the bot could not give their roles — grant them by hand, and `/team lineup` shows who is placed where;
- the notice that the season's posts were being made, where it could not be posted;
- a lineup that was not posted — no command posts one again, and it is posted with the next change to that division's drivers;
- a calendar that was not posted — post it with `/division calendar-sync`;
- opening standings and attendance sheets that were not posted — no command posts them again, and each round's results and attendance post them as usual;
- part of a division's opening standings left standing when the job stopped, naming the channel and the message — delete it by hand;
- that notice, where it could not be deleted, with its link — delete it by hand.

The same list goes to the log channel. A driver who has left the server is simply passed over.

> **If your confirmation never arrives**, look in the channel you ran the review in. Discord gives the bot fifteen minutes to answer a button, and an approval that waits on a stopped queue can outlast them, as can a restart of the bot while it ran. The bot then posts one line there instead, mentioning you: whether the season was approved, and that the log channel has the rest. The same line follows an approval refused when its turn came on the queue — the season, its dates, its channels or the drawing program having changed while it waited — with the reason it was refused, and one a league admin discarded before it began, saying it was not approved.

**Confirming a mid-season window's placements works the same way.** The placements stand once confirmed. Anything left undone is listed in your confirmation: a driver the bot could not give their roles, whom you give them by hand; a lineup it could not post, which is posted with the next change to that division's drivers; or a season it could not return to ongoing, which you put right by running `/season placements-review` and confirming again. The same one-line notice stands in for a confirmation that cannot reach you.

> **The opening classification needs the results and attendance modules, not the images module.**
> Steps 4 and 5 draw nothing without `/images`; step 6 posts either way — as a drawing where the
> `standings` and `attendance` outputs are switched on, and as the ordinary text tables where they
> are not. It goes to the channels those modules already use, so a division with no standings
> channel simply gets nothing. A division whose sheets will not post stops the queue until it is
> retried, and once a league admin discards it, it is named in your confirmation.

---

## Step 13 — Running the season

```
/season status
```

Shows where each division has got to and what its next round is. This one only needs the interaction role, so you can let more people run it.

Things change. While the season is ongoing:

| Command | What it does |
|---|---|
| `/round amend` | Change a round's track, time or format — any combination of them, judged and applied as one change. Changing the time renumbers the division's rounds, and the new time must still be ahead — a round is never moved into the past. Forecasts are thrown away only where the round has moved far enough that they would not have been drawn yet; the confirmation tells you which before you commit. Giving it the values it already holds changes nothing, and it says so; a cancelled round, or one with its results in, is refused whatever you give, and so is every amendment of any round of a division, track and format included, while a cancellation of the division or of any of its rounds is on the change queue, naming the job — chiefly because a new time renumbers the rounds |
| `/round cancel` | Call off one round. Needs `CONFIRM`. Acknowledged at once and carried out on the change queue, the reply updated when it is done. The bot announces nothing itself — tell your drivers — but the check-in channel carries a notice where attendance is on, and the calendar is reposted with the round struck through. Refused once the round's results have been entered — the drivers' reports and appeals depend on them |
| `/division cancel` | Call off a whole division. Needs `CONFIRM`. Acknowledged at once and carried out on the change queue. Every round of it whose results have not been entered is cancelled with it, one whose submission channel is open included (what was pasted into it is lost); rounds whose results are in keep them. Told as `/round cancel` tells it. Refused for a division that has finished, there being nothing left to call off |
| `/division calendar-sync` | Repost a division's calendar with your changes on it. Refused once every division is done — there is no round left for the calendar to describe |
| `/clean-bot` | Delete the bot's own most recent messages in the command channel. You say how many, up to ten, and nobody else's messages are touched. An approved or expired review clears itself, so this is for whatever else the bot has left behind |

> **The posted calendar does not update itself — except for a cancellation.** It is the calendar the season was approved with, and it stays that way until you cancel a round, a division or the season, which reposts it with the cancelled rounds marked. `/round amend` changes what the bot *does*, but the picture or the message your drivers scroll back to is untouched until you run `/division calendar-sync`. This trips up nearly everyone once.

### Changing the lineup

| Command | What it does |
|---|---|
| `/driver move` | Move a driver from their seat to another team, in the same division or another — one change, roles and lineups included |
| `/driver release` | Take a driver out of one division while they keep their other seats |
| `/driver sack` | Remove a driver from the season altogether. A league admin's |

Each takes effect at once, roles and lineup posts included, because the placement it changes is already confirmed. A driver moved, released or sacked keeps their place in the season's history: when the season ends they get an entry for every division they took part in, the ones they left included. `/driver release` is refused for a driver's only confirmed seat — a placement not yet confirmed elsewhere does not count — so move or sack them instead.

### Changing the account behind a driver

A driver who loses their Discord account, or moves to a new one, keeps everything they have
earned. Make the new account their current one:

```
/driver reassign new_user:@TheirNewAccount old_user_id:123456789012345678
```

Name the driver by any account of theirs — with `old_user` where it is still in the server, or
with `old_user_id`, the raw snowflake, where it has gone, which is the usual case.

The old account becomes one of the driver's **past** accounts. Nothing is rewritten: every
result, standing and history entry keeps the account it was recorded under, and a completed
season is left exactly as it was. The bot counts the driver once all the same, and names them by
their new account in everything it draws or posts from then on. A result pasted, a penalty given
or a check-in pressed under the old account still counts as theirs.

Things to know before you run it:

- **You can only run it while you are placing drivers** — while the season is in placements,
  or mid-season while the drivers of a closed signup window are being placed. Everywhere else
  it is refused, including between seasons. Once the season is being raced, `/driver move` is
  the command for changing where a driver sits. So a driver who changes account in the close
  season is re-keyed when the next season reaches placements, which is when you would be
  placing them anyway — do it then, before you seat them.
- **Finish any signup in progress first.** The command is refused while the driver, or the new
  account, is collecting answers, in review or in correction. Approve it, reject it or have it
  withdrawn, then run the command.
- **The new account must be in the server,** and it cannot be another driver's past account.
  Nothing is changed by a refusal.
- **If the new account has signed up already, the two merge.** This is the usual case: the
  driver signed up again on the new account before you noticed. Deal with that signup first —
  reject it if the driver is still seated on the old account — then run the command. The two
  become one driver; the one holding the live season's seat or signup is kept and the other's
  results and history join it. It is refused where both hold the live season, or where both
  took part in the same division of the same season, since one person cannot stand twice in
  one season's standings.
- **The roles follow the account.** The signed-up, division and team roles move to the new
  account and come off the old one, if it is still in the server. The reply lists anything
  Discord would not let the bot do — usually a role above the bot's own.
- **So does a signup channel still held open** after its approval or rejection: it moves to the
  new account and is deleted when it was already due. If the new account has one of its own,
  that one is kept and the old one is deleted at once.
- **You can switch back.** Naming one of the driver's past accounts as `new_user` makes it
  current again.
- **A past account cannot press Sign Up.** It is told to use the current account; switch back
  first if the driver wants to use the old one again.
- **Their portrait is fetched again, not carried.** A portrait is the picture of the account
  itself, so the old one is discarded and the new account's own is taken before the next
  graphic is drawn. A portrait you put in the driver directory yourself is never touched.

### Signing up drivers mid-season

A signup window can be opened again while the season is ongoing. The same two steps follow it: closing the window moves the season to **Ongoing, placements** where anyone is left to settle — place or reject them as in step 10, then run `/season placements-review`, which in this stage reviews only the lineups and the drivers to confirm. Confirming grants the new drivers their roles, posts each lineup that changed once, and returns the season to ongoing; a press turned away leaves the review up to press again, as in step 12. No division or round can be added mid-season.

**The mid-season review checks what can have changed since the season started.** It withholds its button, telling you why, while a signup is unsettled, a channel a division posts to has been deleted, the image module's configuration is at fault — the rasteriser, a template, the tier colours or the portrait settings — or a lineup it will post does not draw with the new drivers in it. Where lineups are pictures, it shows you each one it will post, new drivers included. It does not check the roles, the signup settings, the team list or the points again: the season settled those at the start. A deleted channel is put back as in step 9; nothing else about the season has to change.

Where the window closes with nobody left to settle, the season goes straight back to ongoing.

---

## Step 14 — Ending a season

```
/season complete
```

**Nothing ends a season by itself.** Once every division is finished or cancelled, the season moves to **pending completion** on its own, and `/season complete` is what ends it. Before then the bot refuses, listing the outstanding rounds. It refuses as well while a round's results are being amended, naming the round and its channel — finish or cancel that amendment first. A mid-season signup window does not hold it up: with every division done there is no round left to place anyone into, so the window is closed and every pending placement turned down — each unplaced or unconfirmed driver returns to Not Signed Up, as `/driver reject` would. From pending completion there are three things left and no others: **amending the results of a round already final**, **repairing a division's channels** — the final classification and the final attendance sheet are posted to them, so one deleted has to be repointed — and completing the season. `/division calendar-sync`, `/team modify`, `/team reserve-role`, the `/results` syncs, the reserves toggle and every `/results amend` command are refused, and no module can be turned off. Completing then ends the season: each division's **final classification** is posted, a history entry is written for every division each driver took part in (one they were moved or released from included), the season's roles are revoked, an open signup window is closed, and every driver returns to **Not Signed Up** — ready to sign up for the next season. Drivers who never raced are deleted at this point, their signups kept with the season; former drivers are kept. Test mode is switched off, and the season is archived and announced in the log channel.

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
> nothing should be able to delete it. Unlike approval, where a sheet that will not post stops the
> queue, here a division whose sheets will not post is named in the log channel and the season
> completes regardless.

An archived season cannot be edited. Start the next one with `/season setup` and a new game edition; your team list, your modules and your `/bot init` settings all carry over.

**You cannot build next season while this one is still live.** A server holds one live season at a time, whatever its stage, so the season has to be completed, cancelled or aborted before `/season setup` will start another. Archived seasons are not live and never get in the way: every completed and cancelled season you have ever run stays in the database, with its rounds, results, standings and driver histories intact, and the stats commands keep reading them.

If you need to stop a season rather than finish it, which command depends on whether its placements were ever confirmed.

```
/season cancel confirm:CONFIRM
```

> ⚠️ **`/season cancel` is irreversible, and it is not `/season complete` with a different name.**
> It cascades: every division is cancelled, and with each one every round you have not yet raced.
> Rounds you *have* raced keep their results — cancelling never throws a result away — and the
> season is archived rather than deleted. A round whose results were in but whose reports or
> appeals were still open is closed as final on the way, because those verdict commands are
> refused once the season is cancelled and the round would otherwise wait for ever; the drivers
> in it count as having raced, so their profiles are kept. Every driver still gets a history entry for each division
> they took part in — a cancelled season is league history, marked as cancelled so it can be told apart
> from one that ran to its end — and then, as on completion, every driver returns to Not Signed Up
> and those who never raced are deleted. What you lose is the ending: no final classification is
> posted. It is available only while the season is ongoing, and not while a round's results are
> being amended — finish or cancel that amendment first, or the history would carry its corrections.

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

There are two ways, and they are very different. **`/bot pack`** moves your league to another
server and keeps it. **`/bot factory-reset`** erases it.

### Moving the league to another server

Your drivers, past seasons, team list, points configurations and module settings all come with
you. The old server's channels and roles do not, so you set those again on the new one.

1. **Finish the season you are running.** `/bot pack` is refused while a season is current — at
   any stage short of completed or cancelled. Complete it, or cancel or abort it.
   `/bot pack` is refused too while the change queue holds a job, naming it. Let the queue
   finish; if it is stopped at a job, press Retry on that job's notice in the log channel, or
   Discard if you are a league admin, as in [When a job stops the queue](#when-a-job-stops-the-queue).
2. **Pack, in the command channel:**

   ```
   /bot pack confirm:CONFIRM
   ```

   The bot writes a line to the log channel saying the pack is under way, then lets go of this server. From now until the
   next step, every button and form it has ever posted is refused, wherever it is pressed.
3. **Invite the bot to the new server** and run `/bot init` there, as in step 1. It claims the
   new server and takes the four settings afresh; test mode and your module settings are as you
   left them.
4. **Give each team its role again**, with `/team modify` and `/team reserve-role` — the roles on
   the old server mean nothing on the new one. Set the league's two roles again with
   `/bot base-role` and `/bot driver-role`, the hub with `/bot hub-channel` if you had one,
   and if the signup module is on, its channel with `/signup channel`.
5. **Set up the next season** as normal. Each division takes its role and channels from the new
   server.
6. **Remove the bot from the old server** when you are ready. Its messages there stay, and their
   buttons refuse; nothing on the old server acts on your league any more.

> Test mode does not stop a pack. Test drivers move with the league like everybody else.

### Erasing everything

```
/bot factory-reset confirm:CONFIRM
```

Only the Discord **server owner** can run this, from any channel. It takes a backup of both of
the bot's databases on the computer running it, and erases nothing if the backup fails, saying so in the reply and in the log channel. Then it
erases the league — seasons, drivers, teams, settings, all of it — and deletes the channels the
bot created and every message the bot posted on this server. Nobody else's messages are touched.
When the clean-up ends, the bot leaves one line in the log channel saying who ran the reset and how the clean-up ended; it is the one message of its own that stays. `/bot init` is refused until the clean-up has finished. Afterwards you start again from step 1 of this guide.

The clean-up can take a long while on a server with a long history. The bot sends you a direct
message and keeps it up to date; if that message stops changing, it names the channel the
clean-up stopped in. Getting the backup back is done on the computer running the bot, not by a
command.

---

## Checklist before you confirm placements

- [ ] The bot is running, and its role sits above every role it must grant
- [ ] `/bot init` has been run, both roles are set, and the log channel is one your drivers cannot read
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

## When a job stops the queue

Some changes are carried out as a list of jobs, one after another, each with a number of its own ("job #12"). **Any job that fails stops the queue**, and nothing behind it runs until that job is cleared. Turning `results` off is carried out this way, and so is approving a season, with its arming, grants and posts, and so are `/round cancel` and `/division cancel`, with their notices, call take-downs and calendar repost, and so is a round's review in the results module: opening it, approving its reports and its appeals, and approving either stage of an amendment, with the attendance sheet and each sanction that follows; and approving a points amendment in the results module, with its reposts, sheet and sanctions. The rules below hold for every change that joins them.

You will see:

- **One ❌ message in the log channel**, with **Retry** and **Discard** buttons. It names the job's number, what the job was, the request it belongs to, who asked and the kind of fault. The reply to the request says it is stopped and will be tried again. Managers and admins have to be able to read the log channel to use the buttons.
- **A request asked meanwhile is acknowledged as usual**, joins the back of the queue, and says which job the queue is stopped at. It runs once that job is cleared.

Clear it in this order.

1. **Put right what failed.** Where the fault was a missing channel or a lost permission, set the channel or restore the permission first. Retrying before then only fails again.
2. **Wait, or press Retry.** The bot tries the job again by itself 1, 5, 10, 15, 30 and 60 minutes after it first failed, and says nothing about a try that fails until the last. Then it writes one line saying it has stopped trying on its own, and **only Retry** continues. A league manager or a league admin can press **Retry** on the notice at any time to try the job at once, except while the bot is trying it already: that press is refused, and you press again once the try has ended. A Retry that fails is recorded, naming who pressed it, and leaves the queue stopped.
3. **Or press Discard, as a league admin.** It drops that one job and the queue runs on. The log channel records who discarded it and what was not done, and the change's later jobs still run, each checking it is still due. A league manager's press is refused. What the job leaves undone is named in the reply to the request and in the log channel: for a removal in turning `results` off, or a message a review could not delete, the message with a link, for you to delete by hand; for a post, what was not posted and the command that posts it; for a cancellation's notice or calendar, the channel and the command that reposts it; for a check-in call, its messages, for you to delete by hand; for a verdict, the driver, whose decision you post yourself; for a sanction, the driver and the `/attendance sync` that applies it. Where the job was one a round's review depends on (the approval's save, the opening of the review, or a prompt), the review is asked for again at once, with a fresh prompt in place of the dead one, and the reply says whether to press **Approve** again. See [the results guide](configuring-the-results-module.md#settling-the-round-penalties-then-appeals).

A job that goes through says so, and its notice loses its buttons. So does a stop that clears because the job is found no longer due, or because the request it belonged to is refused at its check.

**After a restart a stopped queue stays stopped.** The bot makes no try of its own, one line in the log channel says so, and only Retry or Discard moves it. A change the bot was part-way through, with no job failed, is finished as the bot starts: a round whose review or approval is on the queue is left to it, and one that never reached the queue is put back through it.

**A request is refused while the same one is in hand.** Approving a stage of a round's review again while the first approval is waiting, running or stopped is refused, and so is `/results rounds amend` for a division with a job of a review on the queue; the refusal names the job. Approving a season again is refused too, without naming the job; cancelling a round or division again, or a round whose division's cancellation is in hand, is refused naming the job. `/results rounds amend` is held by a cancellation in the division as well, and so is every `/round amend` of any round of the division, whatever it changes, chiefly because a new time renumbers the rounds. Let it finish, or press Retry or Discard on its notice. The other way round, `/round cancel` and `/division cancel` are refused while a `/round amend` of a round of the division is being applied, from its Confirm until the rounds are renumbered: try again in a moment.

A button pressed by someone who may not use it, or on a notice whose job no longer stops the queue, is refused, and the log channel records the refusal. So is Retry or Discard pressed while the bot is trying that job: press it again once the try has ended.

## If something looks wrong

| What you see | Usually means |
|---|---|
| A command refuses with a short message only you can see | You are not in the command channel, or you hold neither the interaction role nor the league admin role. Check the channel first — it is almost always the channel |
| A command does not appear in Discord's menu at all | The command list has not reached your server yet. Whoever hosts the bot can push it through immediately with `!sync` |
| "This command is a league admin's" | It asks for the league admin role, which you do not hold. The table at the top of this guide lists which commands those are; someone holding that role has to run them |
| "No league admin role is configured" | Your league was set up before the bot had one. A server administrator can put that right from any channel with `/bot admin-role`, and every league admin command works again |
| The bot placed a driver but the role did not appear | The bot's own role sits below the role it is trying to grant. Move it up |
| `/division add` or `/season placements-review` refused, naming placements | The season is not in placements yet. Confirm its configuration with `/season config-review`, and close its signup window if it has one |
| `/team add` or a signup setting refused, naming the season | The season's configuration is confirmed, which fixes them until it ends |
| Confirmation refuses over tiers | A division was deleted and left a gap. `/division amend` something into it |
| A command setting a division's channel — `/division lineup-channel`, `/weather channel` and the rest — says no season is live | A division's channels belong to the season being built or raced. Once a season ends they no longer count; set the next season's up once its divisions exist |
| "Unknown track" | The circuit name has to match exactly. Use the ID instead, or `/track list` to see the spellings |
| A round appears at the wrong time to your drivers | You entered local time, not UTC. `/round amend` it |
| The calendar in the channel is out of date | It changes by itself only on a cancellation; an amended round needs `/division calendar-sync` |
| A division posts nothing where the others post fine | That division is missing the channel for it. Check step 9 |
| The season will not complete | Some round has not had its appeals review approved. The refusal names them — approve the appeals in each round's submission channel, or cancel a round that will never be raced |
| Drivers placed mid-season were turned down on their own | Every division finished while their placements were still unconfirmed. There was no round left for them, so their placements were discarded and they returned to Not Signed Up |
| "❌ … stopped on a fault in the bot, not on anything you entered" | The bot ran into a fault of its own, not a mistake in what you typed. Where the reply says it may have been partly done, check what the command was meant to change before you run it again. Some commands undo themselves and say so instead — the module is still off, nothing from the paste was saved — with what to do next. The log channel has a line naming the command and the kind of fault: if it happens again, give that line to whoever hosts the bot |
| A ❌ message in the log channel says the queue is stopped at a job, with Retry and Discard buttons | A job failed and nothing behind it runs until it is cleared. Put right what it names, then press Retry; a league admin can press Discard instead. See [When a job stops the queue](#when-a-job-stops-the-queue) |
| A review's approval, an amendment's, a season's, or a cancellation's was acknowledged and its reply never changed | Its job, or one ahead of it, has stopped the queue. Read the ❌ notice in the log channel and clear it as above |
| Nothing at all is happening on schedule | The bot is not running. Starting it again picks up missed weather phases, missed check-in calls whose deadline has not yet passed, missed check-in deadlines, the tidying away of forecasts and check-in messages a day after a round, and a signup auto-close timer; anything else that came due while it was down is missed |

Anything the bot works out, fails to find, falls back on or fails at is written to the log channel. So is every outcome of a command, button or form that changes something, whoever used it: what it set, what it refused and why (a line starting ⛔), and a confirmation cancelled (↩️) or left to lapse (⌛), each naming the member. A refusal made before `/bot init` or after `/bot pack`, in a direct message, or by someone on another server is answered as ever but goes to the host's log alone, not to this channel. When something is behaving oddly and this table has not explained it, read that channel — the answer is nearly always sitting in it.

**If the log channel itself cannot take a line,** the bot keeps the line and delivers it later, and tells you so with "⚠️ Failed to write to log channel (id=…). Please check bot permissions." in a reply only you can see, not in the channel where you gave the command. You are told once, however many of your lines fail, and after the reply to your command where it has not been answered yet. Where nobody's command can still be answered, such as a timed job, only the host's log records it. Check the bot's permissions in the log channel.
